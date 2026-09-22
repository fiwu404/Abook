import os

import pymupdf
from celery import Celery
from sqlalchemy import select

from .ai import ocr_png, structured, vision_json
from .catalog import chapter_entries, chapter_for_page, chapter_outline, discover_catalog, headings_for_chunks, normalized
from .db import SessionLocal
from .models import Book, BookCatalog, Chunk, Job, Knowledge, Page, Question
from .provider_catalog import refresh_provider_catalog
from .services import chapter_question_plan, page_issues, rebuild_chunks, scoped_knowledge_with_headings, validate_question


celery_app = Celery("abook", broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"))
celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1,
                       task_reject_on_worker_lost=True,
                       task_soft_time_limit=3600, task_time_limit=3660)
celery_app.conf.beat_schedule = {
    "refresh-model-provider-catalog": {
        "task": "abook.refresh_provider_catalog",
        "schedule": 1800.0,
    },
}


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
            toc = validate_toc(bookmarks, len(pdf))
            pages, raw, warnings, source = [], "", [], "bookmarks"
        else:
            toc, pages, raw, warnings = discover_catalog(pdf, catalog.toc_pages if catalog else [], vision_json)
            source = "directory"
        if not _check(db, job):
            return
        db.refresh(book)
        if book.status == "deleted":
            return
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
    def visible_match(text: str) -> str | None:
        leading = normalized(text[:500])
        matches = [name for name in choices if name != "前置内容" and normalized(name) in leading]
        return matches[0] if len(matches) == 1 else expected if expected in matches else None

    try:
        png = pdf[index].get_pixmap(matrix=pymupdf.Matrix(1.1, 1.1)).tobytes("png")
        result = vision_json(png, prompt)
        match = next((name for name in choices if normalized(name) == normalized(str(result.get("chapter", "")))), None)
        if not match:
            match = visible_match(str(result.get("raw_text", "")))
            if match:
                return match, [f"视觉模型返回了页面文字而非分类 JSON，已按可见标题匹配为「{match}」，请人工核对"]
            return expected, ["视觉章节分类未给出目录中的章节，已按目录暂定，请人工核对"]
        if result.get("confidence") == "low":
            return expected, [f"视觉章节分类不确定：{match}，请人工核对"]
        if match != expected:
            return match, [f"视觉分类为「{match}」，与目录映射「{expected}」不同，请核对章节边界"]
        return match, []
    except Exception as exc:
        match = visible_match(pdf[index].get_text()[:500])
        if match:
            return match, [f"视觉请求失败；已按 PDF 可见标题匹配为「{match}」，请人工核对。原因：{str(exc)[:180]}"]
        return expected, [f"视觉章节分类失败，已按目录暂定为「{expected}」，请人工核对。原因：{str(exc)[:180]}"]


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
        if not _check(db, job):
            return
        db.refresh(book)
        if book.status == "deleted":
            return
        book.status = "parsed"
        catalog.classified = True
        db.commit()


def _source_weight(value: str) -> int:
    # Chinese OCR text consumes more model context than the same number of ASCII characters.
    return len(value) + sum(ord(char) > 127 for char in value)


def _source_parts(value: str, budget: int = 3200):
    remaining = value.strip()
    while _source_weight(remaining) > budget:
        weight = 0
        end = 0
        for char in remaining:
            weight += 1 + (ord(char) > 127)
            if weight > budget:
                break
            end += 1
        boundary = max((remaining.rfind(mark, end // 2, end) for mark in ("\n", "。", "；", ";", ".")), default=-1)
        if boundary >= end // 2:
            end = boundary + 1
        yield remaining[:end].strip()
        remaining = remaining[max(0, end - 80):].strip()
    if remaining:
        yield remaining


def _knowledge_batches(chunks, headings: dict[int, int]):
    batches = []
    current = []
    current_heading = None
    size = 0
    for chunk in chunks:
        heading = headings[chunk.id]
        for part in _source_parts(chunk.text):
            weight = _source_weight(part)
            if current and (heading != current_heading or size + weight > 3200):
                batches.append((current_heading, current))
                current, size = [], 0
            current_heading = heading
            current.append((chunk, part))
            size += weight
    if current:
        batches.append((current_heading, current))
    return batches


def _verbatim_quote(source: str, proposed: str) -> str:
    """Recover the literal OCR span when a model only changes whitespace."""
    proposed = proposed.strip()
    if proposed in source:
        return proposed
    letters = [(index, char) for index, char in enumerate(source) if not char.isspace()]
    needle = "".join(char for char in proposed if not char.isspace())
    if not needle:
        return ""
    position = "".join(char for _, char in letters).find(needle)
    if position < 0:
        return ""
    return source[letters[position][0]:letters[position + len(needle) - 1][0] + 1]


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
    pages = {page.id: page for page in db.scalars(select(Page).where(Page.book_id == book.id)).all()}
    headings = headings_for_chunks(book.toc, chapter, chunks, pages)
    groups = _knowledge_batches(chunks, headings)
    job.total = len(groups)
    db.commit()
    for index, (heading, group) in enumerate(groups):
        if not _check(db, job):
            return
        source = "\n".join(f"[原文块 {chunk.id}] {part}" for chunk, part in group)
        heading_title = book.toc[heading]["title"]
        data = structured(
            f"细致提取教材章节《{chapter}》、目录小节《{heading_title}》的可独立学习和考查的知识点。"
            "逐段阅读原文；定义、术语、组成、分类、原理、因果关系、步骤、适用条件、公式含义、"
            "注意事项和容易混淆的区别，只要原文明确支持，就分别成条，不要只概括几个大点。"
            "内容应解释清楚具体事实和条件，标题要具体；内容丰富的片段尽量提取 3 到 8 条，"
            "但不要为凑数量而编造、重复或扩展到教材之外。忽略页眉、目录、练习题及纯过渡文字。"
            "知识点按目录章节归属，不按 PDF 页码归属。"
            "返回 JSON {\"items\":[{\"chunk_id\":原文块数字ID,\"title\":\"\",\"content\":\"\",\"source_quote\":\"该块中的连续原文摘录\"}]}。"
            "每条只讲一个明确概念，绑定对应原文块，并提供能直接证明内容的简短逐字连续摘录；"
            "没有可靠内容则返回空数组。\n" + source)
        if not _check(db, job):
            return
        by_id = {chunk.id: chunk for chunk, _ in group}
        source_parts = {}
        for chunk, part in group:
            source_parts.setdefault(chunk.id, []).append(part)
        items = data.get("items", [])
        for item in items[:20] if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                chunk = by_id[int(item.get("chunk_id", 0))]
            except (KeyError, TypeError, ValueError):
                continue
            quote = next((found for part in source_parts[chunk.id]
                          if (found := _verbatim_quote(part, str(item.get("source_quote", ""))))), "")
            if not quote or quote not in chunk.text:
                continue
            title = str(item.get("title", "")).strip()[:200]
            if not title or db.scalar(select(Knowledge.id).where(Knowledge.chunk_id == chunk.id,
                                                           Knowledge.origin == "ai", Knowledge.title == title,
                                                           Knowledge.status != "deleted")):
                continue
            db.add(Knowledge(book_id=book.id, chunk_id=chunk.id,
                             title=title,
                             content=str(item.get("content", "")), source_quote=quote,
                             chapter=chapter, origin="ai"))
        job.progress = index + 1
        db.commit()


def _create_question(db, knowledge: Knowledge, variant_of=None, scope_titles: list[str] | None = None):
    if not knowledge or knowledge.status != "approved" or not knowledge.chunk_id:
        raise RuntimeError("仅可从已审核、绑定教材原文的知识点出题")
    chunk = db.get(Chunk, knowledge.chunk_id)
    book = db.get(Book, knowledge.book_id)
    if not book or not book.mapping_confirmed or not chunk or not chunk.active or chunk.chapter != knowledge.chapter:
        raise RuntimeError("教材内容已变更，请重新审核知识点")
    original = db.get(Question, variant_of) if variant_of else None
    variation = ("与原题考察相同知识点，但题干和选项不同。原题：" + original.stem + "\n") if original else ""
    source = knowledge.source_quote
    if not source or source not in chunk.text:
        raise RuntimeError("知识点的原文摘录已失效，请先修正并重新审核知识点")
    scope = ("所选目录标题：" + "、".join(scope_titles[:20]) + "。仅使用下方知识点逐字摘录作为依据。") if scope_titles else ""
    prompt = ("仅根据教材章节《" + knowledge.chapter + "》的已审核知识点生成 1 道单选题。" + variation +
        scope + "不得引用其他章节，也不要按 PDF 页码出题。返回 JSON：{\"stem\":\"\",\"options\":{\"A\":\"\",\"B\":\"\",\"C\":\"\",\"D\":\"\"},\"answer\":\"A\",\"explanation\":\"\",\"evidence\":\"原文连续摘录\",\"difficulty\":\"easy|medium|hard\"}。"
        "只考教材范围，只有一个正确答案。evidence 必须直接复制【教材原文】中的连续文字，不能概括、改写、拼接或引用知识点总结。"
        "题干、答案和解析也必须能由该摘录直接支持。\n【教材原文】\n" + source +
        "\n【已审核知识点提示，仅用于确定考点】\n" + knowledge.title + " " + knowledge.content)
    data = structured(prompt)
    evidence = _verbatim_quote(source, str(data.get("evidence", "")))
    if not evidence:
        data = structured("上一次生成的依据不是教材原文中的连续文字。请重新生成整道题，"
                          "题干与正确答案必须由原文直接支持，evidence 只能从【教材原文】复制一段连续文字。\n" + prompt)
        evidence = _verbatim_quote(source, str(data.get("evidence", "")))
    db.refresh(book)
    db.refresh(knowledge)
    if book.status == "deleted" or knowledge.status != "approved" or not book.mapping_confirmed:
        raise RuntimeError("教材或知识点已删除或变更，停止生成题目")
    question = Question(knowledge_id=knowledge.id, chunk_id=chunk.id,
                        stem=str(data.get("stem", "")), options=data.get("options", {}),
                        answer=str(data.get("answer", "")), explanation=str(data.get("explanation", "")),
                        evidence=evidence or str(data.get("evidence", "")), difficulty=str(data.get("difficulty", "medium")),
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
    outline = chapter_outline(book.toc, chapter)
    selected = (job.payload or {}).get("selected_heading_indices") or [outline[0]["index"]]
    knowledge, _, heading_by_knowledge = scoped_knowledge_with_headings(db, book, chapter, selected)
    if not knowledge:
        raise RuntimeError("所选目录标题下没有已审核且有原文出处的知识点")
    count = int((job.payload or {}).get("count", 1))
    plan = chapter_question_plan(db, knowledge, heading_by_knowledge, count)
    job.total = count
    db.commit()
    for index, (point, heading) in enumerate(plan):
        if not _check(db, job):
            return
        _create_question(db, point, scope_titles=[chapter, book.toc[heading]["title"]])
        job.progress = index + 1
        db.commit()


@celery_app.task(bind=True, name="abook.run_job", max_retries=2)
def run_job(self, job_id: int):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status in {"done", "cancelled"}:
            return
        if job.cancel_requested:
            job.status = "cancelled"
            db.commit()
            return
        job.status = "running"
        job.error = ""
        db.commit()
        try:
            {"toc": _toc, "parse": _parse, "knowledge": _knowledge, "question": _question,
             "chapter_questions": _chapter_questions}[job.kind](db, job)
            db.refresh(job)
            job.status = "cancelled" if job.cancel_requested else "done"
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            db.refresh(job)
            job.error = str(exc)[:1000]
            if job.cancel_requested:
                job.status = "cancelled"
                db.commit()
                return
            if self.request.retries < self.max_retries and not isinstance(exc, RuntimeError):
                job.status = "retrying"
                db.commit()
                raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1))
            job.status = "failed"
            db.commit()


@celery_app.task(name="abook.refresh_provider_catalog")
def refresh_provider_catalog_task():
    with SessionLocal() as db:
        refresh_provider_catalog(db)
