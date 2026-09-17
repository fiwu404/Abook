import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .catalog import chapter_outline, expand_heading_indices, headings_for_chunks, normalized
from .models import Audit, Chunk, Knowledge, Page, Question


def audit(db: Session, entity: str, entity_id: int, actor_id: int | None, action: str, before=None, after=None):
    db.add(Audit(entity=entity, entity_id=entity_id, actor_id=actor_id, action=action,
                 before=before or {}, after=after or {}))


def page_issues(text: str, blocks: list) -> list[str]:
    issues = []
    if len(text.strip()) < 30:
        issues.append("文本过少，可能需要 OCR 或人工修正")
    if text.count("�") or text.count("□") > 3:
        issues.append("可能存在乱码")
    if any("|" in block.get("text", "") and block.get("text", "").count("|") > 3 for block in blocks):
        issues.append("疑似表格，请核对")
    if re.search(r"[∑∫√≈≤≥]", text):
        issues.append("疑似公式，请核对")
    return issues


def rebuild_chunks(db: Session, page: Page):
    old = db.scalars(select(Chunk).where(Chunk.page_id == page.id)).all()
    old_ids = [item.id for item in old]
    for item in old:
        item.active = False
    if old_ids:
        for knowledge in db.scalars(select(Knowledge).where(Knowledge.chunk_id.in_(old_ids))).all():
            knowledge.status = "needs_review"
            for question in db.scalars(select(Question).where(Question.knowledge_id == knowledge.id)).all():
                question.status = "needs_review"
    # Keep old chunks so published snapshots and edit history remain traceable.
    pieces = [b for b in page.blocks if b.get("text", "").strip()]
    if not pieces and page.text.strip():
        pieces = [{"text": page.text, "bbox": []}]
    texts = [p["text"].strip() for p in pieces]
    for index, piece in enumerate(pieces):
        db.add(Chunk(book_id=page.book_id, page_id=page.id, position=index,
                     chapter=page.chapter, text=texts[index], bbox=piece.get("bbox", []),
                     previous=texts[index - 1][-500:] if index else "",
                     following=texts[index + 1][:500] if index + 1 < len(texts) else ""))


def scoped_knowledge(db: Session, book, chapter: str, selected_heading_indices: list[int]) -> tuple[list[Knowledge], set[int]]:
    """Keep approved knowledge whose original text block belongs to selected headings."""
    selected = expand_heading_indices(book.toc, chapter, selected_heading_indices)
    chunks = db.scalars(select(Chunk).join(Page, Chunk.page_id == Page.id).where(
        Chunk.book_id == book.id, Chunk.chapter == chapter, Chunk.active.is_(True))
        .order_by(Page.pdf_page, Chunk.position)).all()
    pages = {page.id: page for page in db.scalars(select(Page).where(Page.book_id == book.id)).all()}
    headings = headings_for_chunks(book.toc, chapter, chunks, pages)
    by_chunk = {chunk.id: chunk for chunk in chunks}
    outline = chapter_outline(book.toc, chapter)
    candidates = db.scalars(select(Knowledge).where(
        Knowledge.book_id == book.id, Knowledge.chapter == chapter,
        Knowledge.status == "approved", Knowledge.chunk_id.in_(by_chunk)).order_by(Knowledge.id)).all()
    knowledge = []
    for item in candidates:
        chunk = by_chunk[item.chunk_id]
        heading = headings[chunk.id]
        # OCR may put several headings and their text into one block. Locate the
        # reviewed source quote inside that block before selecting its heading.
        text = normalized(chunk.text)
        quote_position = text.find(normalized(item.source_quote)) if item.source_quote else -1
        if quote_position >= 0:
            matches = [(text.find(normalized(entry["title"])), entry["index"]) for entry in outline
                       if entry["pdf_page"] == pages[chunk.page_id].pdf_page]
            preceding = [match for match in matches if 0 <= match[0] <= quote_position]
            if preceding:
                heading = max(preceding)[1]
        if heading in selected:
            knowledge.append(item)
    return knowledge, selected


def validate_question(db: Session, question: Question) -> list[str]:
    errors = []
    if not question.stem.strip():
        errors.append("题干不能为空")
    if set(question.options or {}) != {"A", "B", "C", "D"} or any(not str(v).strip() for v in (question.options or {}).values()):
        errors.append("必须有 A-D 四个非空选项")
    if question.answer not in (question.options or {}):
        errors.append("答案必须对应一个选项")
    if len({str(value).strip() for value in (question.options or {}).values()}) != len(question.options or {}):
        errors.append("选项内容不能重复")
    if question.difficulty not in {"easy", "medium", "hard"}:
        errors.append("难度必须为简单、中等或困难")
    if not question.explanation.strip():
        errors.append("解析不能为空")
    chunk = db.get(Chunk, question.chunk_id)
    knowledge = db.get(Knowledge, question.knowledge_id)
    if not chunk or not knowledge or knowledge.chunk_id != question.chunk_id or knowledge.chapter != chunk.chapter:
        errors.append("知识点与原文出处不匹配")
    elif not question.evidence.strip() or question.evidence.strip() not in chunk.text:
        errors.append("依据必须逐字出现在原文内容块中")
    elif not knowledge.source_quote or question.evidence.strip() not in knowledge.source_quote:
        errors.append("依据必须逐字出现在知识点的原文摘录中")
    if knowledge and knowledge.status != "approved":
        errors.append("知识点尚未通过审核")
    fingerprint = hashlib.sha256(re.sub(r"\s+", "", question.stem).lower().encode()).hexdigest()
    candidates = db.scalars(select(Question).where(Question.id != question.id)).all()
    if any(hashlib.sha256(re.sub(r"\s+", "", q.stem).lower().encode()).hexdigest() == fingerprint for q in candidates):
        errors.append("题干重复")
    return errors


def question_public(question: Question):
    return {"id": question.id, "stem": question.stem, "options": question.options,
            "difficulty": question.difficulty, "knowledge_id": question.knowledge_id}


def question_snapshot(question: Question, score: int):
    return {**question_public(question), "answer": question.answer,
            "explanation": question.explanation, "evidence": question.evidence,
            "chunk_id": question.chunk_id, "revision": question.revision, "score": score}
