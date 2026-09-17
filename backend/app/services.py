import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

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
    if not chunk or not knowledge or knowledge.chunk_id != question.chunk_id:
        errors.append("知识点与原文出处不匹配")
    elif not question.evidence.strip() or question.evidence.strip() not in chunk.text:
        errors.append("依据必须逐字出现在原文内容块中")
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
