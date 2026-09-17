import os

import pymupdf
from celery import Celery
from sqlalchemy import select

from .ai import ocr_png, structured
from .db import SessionLocal
from .models import Book, Chunk, Job, Knowledge, Page, Question
from .services import page_issues, rebuild_chunks, validate_question


celery_app = Celery("abook", broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"))
celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1,
                       task_reject_on_worker_lost=True,
                       task_soft_time_limit=600, task_time_limit=660)


def _check(db, job):
    db.refresh(job)
    if job.cancel_requested:
        job.status = "cancelled"
        db.commit()
        return False
    return True


def _parse(db, job):
    book = db.get(Book, job.target_id)
    with pymupdf.open(book.file_path) as pdf:
        book.page_count = len(pdf)
        book.toc = [{"level": x[0], "title": x[1], "pdf_page": x[2]} for x in pdf.get_toc()]
        job.total = len(pdf)
        db.commit()
        for index in range(len(pdf)):
            if not _check(db, job):
                return
            page = pdf[index]
            existing = db.scalar(select(Page).where(Page.book_id == book.id, Page.pdf_page == index + 1))
            if existing and existing.revised:
                job.progress = index + 1
                db.commit()
                continue
            blocks = [{"text": b[4].strip(), "bbox": list(b[:4])} for b in page.get_text("blocks")
                      if len(b) > 6 and b[6] == 0 and b[4].strip()]
            text = "\n".join(block["text"] for block in blocks)
            ocr_error = ""
            if len(text.strip()) < 30 and os.getenv("OCR_MODEL"):
                try:
                    text = ocr_png(page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).tobytes("png"))
                    blocks = [{"text": text, "bbox": []}]
                except Exception as exc:
                    ocr_error = f"OCR 失败：{str(exc)[:180]}"
            chapter = next((t["title"] for t in reversed(book.toc) if t["pdf_page"] <= index + 1), "未分章")
            issues = page_issues(text, blocks)
            if ocr_error:
                issues.append(ocr_error)
            if existing:
                existing.text, existing.blocks, existing.issues, existing.chapter = text, blocks, issues, chapter
            else:
                db.add(Page(book_id=book.id, pdf_page=index + 1, printed_page=str(index + 1),
                            chapter=chapter, text=text, blocks=blocks, issues=issues))
            job.progress = index + 1
            db.commit()
        book.status = "parsed"
        db.commit()


def _knowledge(db, job):
    book = db.get(Book, job.target_id)
    if not book.mapping_confirmed:
        raise RuntimeError("请先校对并确认目录与页码映射")
    chunks = db.scalars(select(Chunk).where(Chunk.book_id == book.id, Chunk.active.is_(True)).order_by(Chunk.id)).all()
    job.total = len(chunks)
    db.commit()
    for index, chunk in enumerate(chunks):
        if not _check(db, job):
            return
        if db.scalar(select(Knowledge).where(Knowledge.chunk_id == chunk.id, Knowledge.origin == "ai")):
            job.progress = index + 1
            db.commit()
            continue
        data = structured("从以下教材原文提取至多 2 个核心知识点。返回 {\"items\":[{\"title\":\"\",\"content\":\"\",\"source_quote\":\"原文连续摘录\"}]}。没有可靠知识点则返回空数组。章节：" + chunk.chapter + "\n原文：" + chunk.text[:5000])
        for item in data.get("items", [])[:2]:
            quote = str(item.get("source_quote", "")).strip()
            if not quote or quote not in chunk.text:
                continue
            db.add(Knowledge(book_id=book.id, chunk_id=chunk.id,
                             title=str(item.get("title", ""))[:200],
                             content=str(item.get("content", "")), source_quote=quote,
                             chapter=chunk.chapter, origin="ai"))
        job.progress = index + 1
        db.commit()


def _question(db, job):
    knowledge = db.get(Knowledge, job.target_id)
    if not knowledge or knowledge.status != "approved" or not knowledge.chunk_id:
        raise RuntimeError("仅可从已审核、绑定教材原文的知识点出题")
    chunk = db.get(Chunk, knowledge.chunk_id)
    if not chunk.active:
        raise RuntimeError("教材内容已变更，请重新审核知识点")
    job.total = 1
    db.commit()
    if not _check(db, job):
        return
    variant_of = (job.payload or {}).get("variant_of")
    original = db.get(Question, variant_of) if variant_of else None
    variation = ("与原题考察相同知识点，但题干和选项不同。原题：" + original.stem + "\n") if original else ""
    data = structured("基于以下知识点和教材原文生成 1 道单选题。" + variation + "返回 JSON：{\"stem\":\"\",\"options\":{\"A\":\"\",\"B\":\"\",\"C\":\"\",\"D\":\"\"},\"answer\":\"A\",\"explanation\":\"\",\"evidence\":\"原文连续摘录\",\"difficulty\":\"easy|medium|hard\"}。只考教材范围，只有一个正确答案。\n知识点：" + knowledge.title + " " + knowledge.content + "\n原文：" + chunk.text[:5000])
    question = Question(knowledge_id=knowledge.id, chunk_id=chunk.id,
                        stem=str(data.get("stem", "")), options=data.get("options", {}),
                        answer=str(data.get("answer", "")), explanation=str(data.get("explanation", "")),
                        evidence=str(data.get("evidence", "")), difficulty=str(data.get("difficulty", "medium")),
                        variant_of=variant_of)
    db.add(question)
    db.flush()
    question.validation = validate_question(db, question)
    job.progress = 1
    db.commit()


@celery_app.task(bind=True, name="abook.run_job", max_retries=2)
def run_job(self, job_id: int):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.cancel_requested or job.status == "done":
            return
        job.status = "running"
        job.error = ""
        db.commit()
        try:
            {"parse": _parse, "knowledge": _knowledge, "question": _question}[job.kind](db, job)
            if job.status != "cancelled":
                job.status = "done"
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            job.error = str(exc)[:1000]
            if self.request.retries < self.max_retries and not isinstance(exc, RuntimeError):
                job.status = "retrying"
                db.commit()
                raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1))
            job.status = "failed"
            db.commit()
