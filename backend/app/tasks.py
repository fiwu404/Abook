import os

import pymupdf
from celery import Celery
from sqlalchemy import select

from .ai import ocr_png, structured, vision_json
from .catalog import chapter_entries, chapter_for_page, discover_catalog, normalized
from .db import SessionLocal
from .models import Book, BookCatalog, Chunk, Job, Knowledge, Page, Question
from .services import page_issues, rebuild_chunks, validate_question


celery_app = Celery("abook", broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"))
celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1,
                       task_reject_on_worker_lost=True,
                       task_soft_time_limit=3600, task_time_limit=3660)


def _check(db, job):
    db.refresh(job)
    if job.cancel_requested:
        job.status = "cancelled"
        db.commit()
        return False
    return True


def _toc(db, job):
    book = db.get(Book, job.target_id)
    catalog = db.get(BookCatalog, book.id)
    if catalog and catalog.source == "manual" and book.toc:
        job.total = job.progress = 1
        db.commit()
        return
    with pymupdf.open(book.file_path) as pdf:
        book.page_count = len(pdf)
        job.total = 1
        db.commit()
        bookmarks = [{"level": level, "title": title, "pdf_page": page}
                     for level, title, page in pdf.get_toc() if 1 <= page <= len(pdf)]
        if bookmarks:
            from .catalog import validate_toc
            toc = validate_toc(chapter_entries(bookmarks), len(pdf))
            pages, raw, warnings, source = [], "", [], "bookmarks"
        else:
            toc, pages, raw, warnings = discover_catalog(pdf, catalog.toc_pages if catalog else [], vision_json)
            source = "directory"
        book.toc = toc
        if not catalog:
            catalog = BookCatalog(book_id=book.id)
            db.add(catalog)
        catalog.toc_pages, catalog.raw_text, catalog.warnings = pages, raw, warnings
        catalog.source, catalog.confirmed = source, False
        catalog.classified = False
        book.status = "toc_review"
        book.mapping_confirmed = False
        job.progress = 1
        db.commit()


def _visual_chapter(pdf, index: int, toc: list[dict], boundary_pages: set[int]) -> tuple[str, list[str]]:
    page_number = index + 1
    expected = chapter_for_page(toc, page_number)
    if page_number not in boundary_pages:
        return expected, []
    chapters = chapter_entries(toc)
    nearest = sorted(chapters, key=lambda item: abs(item["pdf_page"] - page_number))[:3]
    choices = [item["title"] for item in sorted(nearest, key=lambda item: item["pdf_page"])]
    choices.insert(0, "前置内容")
    prompt = ("根据这页教材的可见标题和正文判断所属章节。目录起始页仅作参考，必须从候选标题中原样选一个；"
              "如果是封面、前言或目录，选前置内容。返回 {\"chapter\":\"候选标题\",\"confidence\":\"high|medium|low\"}。"
              f"PDF 第 {page_number} 页，目录推定 {expected}。候选：{choices}")
    try:
        png = pdf[index].get_pixmap(matrix=pymupdf.Matrix(1.1, 1.1)).tobytes("png")
        result = vision_json(png, prompt)
        match = next((name for name in choices if normalized(name) == normalized(str(result.get("chapter", "")))), None)
        if not match:
            return expected, ["视觉章节分类未给出目录中的章节，请人工核对"]
        if result.get("confidence") == "low":
            return expected, [f"视觉章节分类不确定：{match}，请人工核对"]
        if match != expected:
            return match, [f"视觉分类为「{match}」，与目录映射「{expected}」不同，请核对章节边界"]
        return match, []
    except Exception as exc:
        return expected, [f"视觉章节分类失败：{str(exc)[:180]}"]


def _parse(db, job):
    book = db.get(Book, job.target_id)
    catalog = db.get(BookCatalog, book.id)
    if not book.toc or not catalog or not catalog.confirmed:
        raise RuntimeError("请先解析并人工确认目录，再解析章节正文")
    with pymupdf.open(book.file_path) as pdf:
        book.page_count = len(pdf)
        boundary_pages = {item["pdf_page"] for item in chapter_entries(book.toc)}
        for item in chapter_entries(book.toc):
            if catalog.source == "manual" or any(item["title"] in warning or "偏移未核实" in warning
                                                 for warning in (catalog.warnings or [])):
                boundary_pages.update(page for page in (item["pdf_page"] - 1, item["pdf_page"] + 1)
                                      if 1 <= page <= len(pdf))
        job.total = len(pdf)
        db.commit()
        for index in range(len(pdf)):
            if not _check(db, job):
                return
            page = pdf[index]
            existing = db.scalar(select(Page).where(Page.book_id == book.id, Page.pdf_page == index + 1))
            chapter, chapter_issues = _visual_chapter(pdf, index, book.toc, boundary_pages)
            if existing and existing.revised:
                text, blocks = existing.text, existing.blocks
                issues = [item for item in existing.issues if not item.startswith("视觉章节分类") and not item.startswith("视觉分类为")]
            else:
                blocks = [{"text": b[4].strip(), "bbox": list(b[:4])} for b in page.get_text("blocks")
                          if len(b) > 6 and b[6] == 0 and b[4].strip()]
                text = "\n".join(block["text"] for block in blocks)
                ocr_error = ""
                if len(text.strip()) < 30:
                    try:
                        text = ocr_png(page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).tobytes("png"))
                        blocks = [{"text": text, "bbox": []}]
                    except Exception as exc:
                        ocr_error = f"OCR 失败：{str(exc)[:180]}"
                issues = page_issues(text, blocks)
                if ocr_error:
                    issues.append(ocr_error)
            issues.extend(chapter_issues)
            if existing:
                existing.text, existing.blocks, existing.issues, existing.chapter = text, blocks, issues, chapter
            else:
                db.add(Page(book_id=book.id, pdf_page=index + 1, printed_page=str(index + 1),
                            chapter=chapter, text=text, blocks=blocks, issues=issues))
            job.progress = index + 1
            db.commit()
        book.status = "parsed"
        catalog.classified = True
        db.commit()


def _knowledge(db, job):
    book = db.get(Book, job.target_id)
    if not book.mapping_confirmed:
        raise RuntimeError("请先校对并确认目录与页码映射")
    chapter = (job.payload or {}).get("chapter", "")
    if chapter not in {item["title"] for item in chapter_entries(book.toc)}:
        raise RuntimeError("请选择目录中的一个章节")
    chunks = db.scalars(select(Chunk).where(Chunk.book_id == book.id, Chunk.chapter == chapter,
                                            Chunk.active.is_(True)).order_by(Chunk.page_id, Chunk.position)).all()
    if not chunks:
        raise RuntimeError("该章节没有可用原文，请先核对章节分类")
    groups = []
    current, size = [], 0
    for chunk in chunks:
        if current and size + len(chunk.text) > 12000:
            groups.append(current)
            current, size = [], 0
        current.append(chunk)
        size += len(chunk.text)
    if current:
        groups.append(current)
    job.total = len(groups)
    db.commit()
    for index, group in enumerate(groups):
        if not _check(db, job):
            return
        source = "\n".join(f"[原文块 {chunk.id}] {chunk.text}" for chunk in group)
        data = structured(
            f"仅总结教材章节《{chapter}》中的核心知识点。这批原文可能跨越多个 PDF 页，页码不是知识单元。"
            "请综合本章上下文，避免把页眉、目录、练习题或重复内容当知识点。"
            "返回 JSON {\"items\":[{\"chunk_id\":原文块数字ID,\"title\":\"\",\"content\":\"\",\"source_quote\":\"该块中的连续原文摘录\"}]}。"
            "每条必须绑定一个原文块；没有可靠内容则返回空数组。\n" + source)
        by_id = {chunk.id: chunk for chunk in group}
        for item in data.get("items", [])[:12]:
            try:
                chunk = by_id[int(item.get("chunk_id", 0))]
            except (KeyError, TypeError, ValueError):
                continue
            quote = str(item.get("source_quote", "")).strip()
            if not quote or quote not in chunk.text:
                continue
            title = str(item.get("title", "")).strip()[:200]
            if not title or db.scalar(select(Knowledge.id).where(Knowledge.chunk_id == chunk.id,
                                                           Knowledge.origin == "ai", Knowledge.title == title)):
                continue
            db.add(Knowledge(book_id=book.id, chunk_id=chunk.id,
                             title=title,
                             content=str(item.get("content", "")), source_quote=quote,
                             chapter=chapter, origin="ai"))
        job.progress = index + 1
        db.commit()


def _create_question(db, knowledge: Knowledge, variant_of=None):
    if not knowledge or knowledge.status != "approved" or not knowledge.chunk_id:
        raise RuntimeError("仅可从已审核、绑定教材原文的知识点出题")
    chunk = db.get(Chunk, knowledge.chunk_id)
    book = db.get(Book, knowledge.book_id)
    if not book or not book.mapping_confirmed or not chunk or not chunk.active or chunk.chapter != knowledge.chapter:
        raise RuntimeError("教材内容已变更，请重新审核知识点")
    original = db.get(Question, variant_of) if variant_of else None
    variation = ("与原题考察相同知识点，但题干和选项不同。原题：" + original.stem + "\n") if original else ""
    data = structured("仅根据教材章节《" + knowledge.chapter + "》的已审核知识点生成 1 道单选题。" + variation +
        "不得引用其他章节，也不要按 PDF 页码出题。返回 JSON：{\"stem\":\"\",\"options\":{\"A\":\"\",\"B\":\"\",\"C\":\"\",\"D\":\"\"},\"answer\":\"A\",\"explanation\":\"\",\"evidence\":\"原文连续摘录\",\"difficulty\":\"easy|medium|hard\"}。只考教材范围，只有一个正确答案。答案依据必须逐字来自下列原文块。\n知识点：" + knowledge.title + " " + knowledge.content + "\n本章原文块：" + chunk.text[:5000])
    question = Question(knowledge_id=knowledge.id, chunk_id=chunk.id,
                        stem=str(data.get("stem", "")), options=data.get("options", {}),
                        answer=str(data.get("answer", "")), explanation=str(data.get("explanation", "")),
                        evidence=str(data.get("evidence", "")), difficulty=str(data.get("difficulty", "medium")),
                        variant_of=variant_of)
    db.add(question)
    db.flush()
    question.validation = validate_question(db, question)
    return question


def _question(db, job):
    knowledge = db.get(Knowledge, job.target_id)
    job.total = 1
    db.commit()
    if not _check(db, job):
        return
    _create_question(db, knowledge, (job.payload or {}).get("variant_of"))
    job.progress = 1
    db.commit()


def _chapter_questions(db, job):
    book = db.get(Book, job.target_id)
    if not book or not book.mapping_confirmed:
        raise RuntimeError("请先确认章节索引")
    chapter = (job.payload or {}).get("chapter", "")
    if chapter not in {item["title"] for item in chapter_entries(book.toc)}:
        raise RuntimeError("请选择目录中的一个章节")
    knowledge = db.scalars(select(Knowledge).where(Knowledge.book_id == book.id,
        Knowledge.chapter == chapter, Knowledge.status == "approved", Knowledge.chunk_id.is_not(None)).order_by(Knowledge.id)).all()
    if not knowledge:
        raise RuntimeError("本章尚无已审核且有原文出处的知识点")
    count = int((job.payload or {}).get("count", 1))
    job.total = count
    db.commit()
    for index in range(count):
        if not _check(db, job):
            return
        _create_question(db, knowledge[index % len(knowledge)])
        job.progress = index + 1
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
            {"toc": _toc, "parse": _parse, "knowledge": _knowledge, "question": _question,
             "chapter_questions": _chapter_questions}[job.kind](db, job)
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
