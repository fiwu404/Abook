import hashlib
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ai import (config_view, encrypt_key, list_models, provider_view, seed_ai_settings,
                 test_model, validate_base_url)
from .db import Base, SessionLocal, engine, get_db, now
from .models import AISettings, Attempt, Audit, Book, Chunk, Job, Knowledge, ModelProvider, Page, Paper, Practice, Question, User
from .security import bootstrap, check_password, current_user, hash_password, issue_token, roles
from .services import audit, page_issues, question_public, question_snapshot, rebuild_chunks, validate_question
from .tasks import run_job


@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(os.getenv("APP_SECRET", "")) < 32:
        raise RuntimeError("APP_SECRET 至少需要 32 个字符")
    Path(os.getenv("UPLOAD_DIR", "./data/uploads")).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        bootstrap(db)
        seed_ai_settings(db)
    yield


app = FastAPI(title="Abook 教材与考试闭环", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def out(value):
    if isinstance(value, list):
        return [out(x) for x in value]
    return jsonable_encoder({column.name: getattr(value, column.name) for column in value.__table__.columns})


def book_view(book: Book):
    data = out(book)
    data.pop("file_path", None)
    return data


def require(value, message="记录不存在"):
    if value is None:
        raise HTTPException(404, message)
    return value


def enqueue(db: Session, kind: str, target: int, key: str, payload=None):
    job = db.scalar(select(Job).where(Job.key == key))
    if job and job.status in ("queued", "running", "retrying", "done"):
        return job
    if job:
        job.status, job.progress, job.total, job.error, job.cancel_requested = "queued", 0, 0, "", False
        job.payload = payload or {}
    else:
        job = Job(key=key, kind=kind, target_id=target, payload=payload or {})
        db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.scalar(select(Job).where(Job.key == key))
    db.refresh(job)
    try:
        result = run_job.apply_async(args=[job.id])
        job.celery_id = result.id
        db.commit()
        db.refresh(job)
    except Exception as exc:
        job.status, job.error = "failed", f"任务入队失败：{exc}"
        db.commit()
    return job


class Credentials(BaseModel):
    username: str
    password: str


class NewUser(Credentials):
    role: str


@app.post("/api/users")
def create_user(data: NewUser, actor: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    if not re.fullmatch(r"[A-Za-z0-9_]{3,40}", data.username) or len(data.password) < 8:
        raise HTTPException(400, "用户名须为 3-40 位字母、数字或下划线，密码至少 8 位")
    if data.role not in {"teacher", "student"}:
        raise HTTPException(400, "仅可创建教师或学生账号")
    user = User(username=data.username, password_hash=hash_password(data.password), role=data.role)
    db.add(user)
    try:
        db.flush()
        audit(db, "user", user.id, actor.id, "create", after={"username": user.username, "role": user.role})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "用户名已存在")
    return {"id": user.id, "username": user.username, "role": user.role}


@app.get("/api/users")
def list_users(actor: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    return [{"id": user.id, "username": user.username, "role": user.role}
            for user in db.scalars(select(User).order_by(User.id)).all()]


@app.post("/api/auth/login")
def login(data: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == data.username))
    if not user or user.role not in {"admin", "teacher", "student"} or not check_password(data.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": issue_token(user), "user": {"id": user.id, "username": user.username, "role": user.role}}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/ai/status")
def ai_status(user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    config = config_view(db)
    providers = {provider.id: provider for provider in db.scalars(select(ModelProvider)).all()}
    details = {}
    for kind in ("text", "vision"):
        provider = providers.get(config[f"{kind}_provider_id"])
        details[kind] = {"provider": provider.display_name if provider else "", "model": config[f"{kind}_model"],
                         "base_url": provider.base_url if provider else ""}
    return details


class ProviderInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    api_style: str = "openai"
    api_key: str = ""


def provider_data(data: ProviderInput):
    if data.api_style not in {"ollama", "openai"}:
        raise HTTPException(400, "接口类型仅支持 Ollama 或 OpenAI 兼容")
    try:
        base_url = validate_base_url(data.base_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data.display_name.strip():
        raise HTTPException(400, "显示名称不能为空")
    return base_url


@app.get("/api/providers")
def providers(user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    return [provider_view(p) for p in db.scalars(select(ModelProvider).order_by(ModelProvider.id)).all()]


@app.post("/api/providers")
def create_provider(data: ProviderInput, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    base_url = provider_data(data)
    provider = ModelProvider(display_name=data.display_name.strip(), base_url=base_url, api_style=data.api_style,
                             api_key_cipher=encrypt_key(data.api_key) if data.api_key else None)
    db.add(provider)
    try:
        db.flush()
        audit(db, "provider", provider.id, user.id, "create", after=provider_view(provider))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "提供商显示名称已存在")
    return provider_view(provider)


@app.put("/api/providers/{provider_id}")
def update_provider(provider_id: int, data: ProviderInput, user: User = Depends(roles("admin")),
                    db: Session = Depends(get_db)):
    provider = require(db.get(ModelProvider, provider_id))
    base_url = provider_data(data)
    before = provider_view(provider)
    provider.display_name, provider.base_url, provider.api_style = data.display_name.strip(), base_url, data.api_style
    if data.api_key:
        provider.api_key_cipher = encrypt_key(data.api_key)
    try:
        audit(db, "provider", provider.id, user.id, "edit", before, provider_view(provider))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "提供商显示名称已存在")
    return provider_view(provider)


@app.get("/api/providers/{provider_id}/models")
def provider_models(provider_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    provider = require(db.get(ModelProvider, provider_id))
    try:
        return {"models": list_models(provider)}
    except Exception as exc:
        raise HTTPException(502, f"获取模型列表失败：{str(exc)[:300]}") from exc


class ModelTest(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    vision: bool = False


@app.post("/api/providers/{provider_id}/test")
def check_model(provider_id: int, data: ModelTest, user: User = Depends(roles("admin")),
                db: Session = Depends(get_db)):
    provider = require(db.get(ModelProvider, provider_id))
    try:
        return test_model(provider, data.model, data.vision)
    except Exception as exc:
        raise HTTPException(502, f"模型测试失败：{str(exc)[:300]}") from exc


@app.get("/api/ai/config")
def get_ai_config(user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    return config_view(db)


class AIConfigInput(BaseModel):
    text_provider_id: int
    text_model: str = Field(min_length=1, max_length=200)
    vision_provider_id: int
    vision_model: str = Field(min_length=1, max_length=200)


@app.put("/api/ai/config")
def update_ai_config(data: AIConfigInput, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    for provider_id in {data.text_provider_id, data.vision_provider_id}:
        require(db.get(ModelProvider, provider_id), "提供商不存在")
    settings = require(db.get(AISettings, 1))
    before = config_view(db)
    settings.text_provider_id, settings.text_model = data.text_provider_id, data.text_model.strip()
    settings.vision_provider_id, settings.vision_model = data.vision_provider_id, data.vision_model.strip()
    if not settings.text_model or not settings.vision_model:
        raise HTTPException(400, "请选择文本模型和视觉模型")
    audit(db, "ai_config", 1, user.id, "edit", before, config_view(db))
    db.commit()
    return config_view(db)


@app.post("/api/books")
async def upload_book(title: str, version: str, file: UploadFile = File(...),
                      user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "第一版仅支持 PDF")
    if not title.strip() or not version.strip():
        raise HTTPException(400, "教材名称和版本不能为空")
    maximum = int(os.getenv("MAX_PDF_BYTES", str(100 * 1024 * 1024)))
    body = await file.read(maximum + 1)
    if len(body) > maximum or not body.startswith(b"%PDF-"):
        raise HTTPException(400, "PDF 无效或超过大小限制")
    if db.scalar(select(Book).where(Book.title == title, Book.version == version)):
        raise HTTPException(409, "教材名称与版本已存在")
    sha = hashlib.sha256(body).hexdigest()
    path = Path(os.getenv("UPLOAD_DIR", "./data/uploads")) / f"{sha}.pdf"
    path.write_bytes(body)
    book = Book(title=title.strip(), version=version.strip(), sha256=sha, file_path=str(path))
    db.add(book)
    db.flush()
    audit(db, "book", book.id, user.id, "upload", after={"title": book.title, "version": book.version, "sha256": sha})
    db.commit()
    return book_view(book)


@app.get("/api/books")
def books(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Book).order_by(Book.id.desc())
    if user.role != "admin":
        query = query.where(Book.mapping_confirmed.is_(True))
    return [book_view(book) for book in db.scalars(query).all()]


@app.get("/api/books/{book_id}")
def book_detail(book_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    book = require(db.get(Book, book_id))
    if user.role != "admin" and not book.mapping_confirmed:
        raise HTTPException(404, "教材不存在")
    return book_view(book)


@app.post("/api/books/{book_id}/parse")
def parse_book(book_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    book = require(db.get(Book, book_id))
    return out(enqueue(db, "parse", book.id, f"parse:{book.id}:{book.sha256}"))


@app.get("/api/books/{book_id}/pages")
def pages(book_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    require(db.get(Book, book_id))
    return out(db.scalars(select(Page).where(Page.book_id == book_id).order_by(Page.pdf_page)).all())


class PageEdit(BaseModel):
    printed_page: str
    chapter: str
    text: str
    issues: list[str] = []


class TocItem(BaseModel):
    level: int = Field(ge=1, le=8)
    title: str
    pdf_page: int = Field(ge=1)


@app.put("/api/books/{book_id}/toc")
def edit_toc(book_id: int, items: list[TocItem], user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    book = require(db.get(Book, book_id))
    if any(item.pdf_page > book.page_count or not item.title.strip() for item in items):
        raise HTTPException(400, "书签页码或标题无效")
    before = {"toc": book.toc}
    book.toc = [item.model_dump() for item in sorted(items, key=lambda x: x.pdf_page)]
    pages = db.scalars(select(Page).where(Page.book_id == book_id).order_by(Page.pdf_page)).all()
    for page in pages:
        chapter = next((item["title"] for item in reversed(book.toc) if item["pdf_page"] <= page.pdf_page), "未分章")
        if not page.revised and page.chapter != chapter:
            page.chapter = chapter
            if book.mapping_confirmed:
                rebuild_chunks(db, page)
    audit(db, "book", book.id, user.id, "edit_toc", before, {"toc": book.toc})
    db.commit()
    return book_view(book)


@app.patch("/api/pages/{page_id}")
def edit_page(page_id: int, data: PageEdit, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    page = require(db.get(Page, page_id))
    book = require(db.get(Book, page.book_id))
    before = out(page)
    changed_text = page.text != data.text or page.chapter != data.chapter
    page.printed_page, page.chapter, page.text = data.printed_page, data.chapter, data.text
    page.blocks, page.issues, page.revised = [{"text": data.text, "bbox": []}], data.issues, True
    if changed_text and book.mapping_confirmed:
        rebuild_chunks(db, page)
    audit(db, "page", page.id, user.id, "edit", before, out(page))
    db.commit()
    return out(page)


@app.post("/api/books/{book_id}/confirm-mapping")
def confirm_mapping(book_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    book = require(db.get(Book, book_id))
    pages = db.scalars(select(Page).where(Page.book_id == book_id).order_by(Page.pdf_page)).all()
    if not pages or len(pages) != book.page_count:
        raise HTTPException(400, "解析未完成，不能建立索引")
    previous_number = None
    for page in pages:
        if not page.chapter.strip() or not page.printed_page.strip():
            raise HTTPException(400, f"PDF 第 {page.pdf_page} 页缺少章节或印刷页码")
        issues = [item for item in page.issues if not item.startswith("疑似缺少印刷页")]
        number = int(page.printed_page) if page.printed_page.isdigit() else None
        if number is not None and previous_number is not None and number > previous_number + 1:
            issues.append(f"疑似缺少印刷页 {previous_number + 1}–{number - 1}，请核对原 PDF")
        page.issues = issues
        previous_number = number
        if not db.scalar(select(Chunk).where(Chunk.page_id == page.id, Chunk.active.is_(True))):
            rebuild_chunks(db, page)
    book.mapping_confirmed, book.status = True, "indexed"
    audit(db, "book", book.id, user.id, "confirm_mapping", after={"pages": len(pages)})
    db.commit()
    return book_view(book)


@app.get("/api/books/{book_id}/learning")
def learning(book_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    book = require(db.get(Book, book_id))
    if not book.mapping_confirmed:
        raise HTTPException(400, "教材尚未建立索引")
    chunks = db.scalars(select(Chunk).where(Chunk.book_id == book_id, Chunk.active.is_(True)).order_by(Chunk.page_id, Chunk.position)).all()
    pages = {p.id: p for p in db.scalars(select(Page).where(Page.book_id == book_id)).all()}
    knowledge = db.scalars(select(Knowledge).where(Knowledge.book_id == book_id, Knowledge.status == "approved")).all()
    by_chunk = {}
    for item in knowledge:
        by_chunk.setdefault(item.chunk_id, []).append({"id": item.id, "title": item.title, "content": item.content, "source_quote": item.source_quote})
    return {"book": book_view(book), "sections": [{"chunk_id": c.id, "chapter": c.chapter,
            "pdf_page": pages[c.page_id].pdf_page, "printed_page": pages[c.page_id].printed_page,
            "text": c.text, "bbox": c.bbox, "previous": c.previous, "following": c.following,
            "knowledge": by_chunk.get(c.id, [])} for c in chunks]}


@app.get("/api/jobs")
def jobs(user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    return out(db.scalars(select(Job).order_by(Job.id.desc()).limit(50)).all())


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    job = require(db.get(Job, job_id))
    if job.status in ("queued", "running", "retrying"):
        job.cancel_requested = True
        if job.status == "queued":
            job.status = "cancelled"
        db.commit()
    return out(job)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    job = require(db.get(Job, job_id))
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(400, "只能重试失败或已取消的任务")
    return out(enqueue(db, job.kind, job.target_id, job.key, job.payload))


@app.post("/api/books/{book_id}/extract-knowledge")
def extract_knowledge(book_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    book = require(db.get(Book, book_id))
    if not book.mapping_confirmed:
        raise HTTPException(400, "请先确认目录与页码")
    newest = db.scalar(select(Chunk.id).where(Chunk.book_id == book_id, Chunk.active.is_(True)).order_by(Chunk.id.desc())) or 0
    return out(enqueue(db, "knowledge", book_id, f"knowledge:{book_id}:{newest}"))


@app.get("/api/books/{book_id}/knowledge")
def list_knowledge(book_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    require(db.get(Book, book_id))
    return out(db.scalars(select(Knowledge).where(Knowledge.book_id == book_id).order_by(Knowledge.id.desc())).all())


class KnowledgeInput(BaseModel):
    title: str
    content: str
    chunk_id: int | None = None
    source_quote: str = ""
    chapter: str = ""


@app.post("/api/books/{book_id}/knowledge")
def create_knowledge(book_id: int, data: KnowledgeInput, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    book = require(db.get(Book, book_id))
    chunk = db.get(Chunk, data.chunk_id) if data.chunk_id else None
    if chunk and (chunk.book_id != book.id or not chunk.active):
        raise HTTPException(400, "原文块不属于本教材当前版本")
    if chunk and data.source_quote not in chunk.text:
        raise HTTPException(400, "引用必须逐字来自原文块")
    item = Knowledge(book_id=book.id, chunk_id=data.chunk_id, title=data.title, content=data.content,
                     source_quote=data.source_quote, chapter=data.chapter or (chunk.chapter if chunk else "人工补充"), origin="manual")
    db.add(item)
    db.flush()
    audit(db, "knowledge", item.id, user.id, "create", after=out(item))
    db.commit()
    return out(item)


@app.patch("/api/knowledge/{knowledge_id}")
def edit_knowledge(knowledge_id: int, data: KnowledgeInput, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    item = require(db.get(Knowledge, knowledge_id))
    before = out(item)
    chunk = db.get(Chunk, data.chunk_id) if data.chunk_id else None
    if chunk and (chunk.book_id != item.book_id or not chunk.active or data.source_quote not in chunk.text):
        raise HTTPException(400, "原文引用无效")
    item.title, item.content, item.chunk_id, item.source_quote, item.chapter = data.title, data.content, data.chunk_id, data.source_quote, data.chapter or (chunk.chapter if chunk else "人工补充")
    item.revision += 1
    item.status = "draft"
    for q in db.scalars(select(Question).where(Question.knowledge_id == item.id)).all():
        q.status = "needs_review"
    audit(db, "knowledge", item.id, user.id, "edit", before, out(item))
    db.commit()
    return out(item)


class ReviewInput(BaseModel):
    approve: bool
    note: str = ""


@app.post("/api/knowledge/{knowledge_id}/review")
def review_knowledge(knowledge_id: int, data: ReviewInput, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    item = require(db.get(Knowledge, knowledge_id))
    if data.approve and item.chunk_id:
        chunk = db.get(Chunk, item.chunk_id)
        if not chunk or not chunk.active or not item.source_quote or item.source_quote not in chunk.text:
            raise HTTPException(400, "原文出处失效，请先修正")
    if not item.title.strip() or not item.content.strip():
        raise HTTPException(400, "知识点内容不能为空")
    before = out(item)
    item.status, item.review_note = ("approved" if data.approve else "rejected"), data.note
    audit(db, "knowledge", item.id, user.id, "review", before, out(item))
    db.commit()
    return out(item)


@app.post("/api/knowledge/{knowledge_id}/generate-question")
def generate_question(knowledge_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    item = require(db.get(Knowledge, knowledge_id))
    if item.status != "approved" or not item.chunk_id:
        raise HTTPException(400, "只有已审核且有教材出处的知识点可以出题")
    count = db.scalar(select(func.count(Question.id)).where(Question.knowledge_id == item.id)) or 0
    return out(enqueue(db, "question", item.id, f"question:{item.id}:{item.revision}:{count}"))


class QuestionInput(BaseModel):
    knowledge_id: int
    stem: str
    options: dict[str, str]
    answer: str
    explanation: str
    evidence: str
    difficulty: str = "medium"
    variant_of: int | None = None


@app.get("/api/questions")
def questions(book_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    ids = select(Knowledge.id).where(Knowledge.book_id == book_id)
    return out(db.scalars(select(Question).where(Question.knowledge_id.in_(ids)).order_by(Question.id.desc())).all())


@app.post("/api/questions")
def create_question(data: QuestionInput, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    knowledge = require(db.get(Knowledge, data.knowledge_id))
    if not knowledge.chunk_id:
        raise HTTPException(400, "题目必须绑定教材原文")
    question = Question(knowledge_id=knowledge.id, chunk_id=knowledge.chunk_id, stem=data.stem,
                        options=data.options, answer=data.answer, explanation=data.explanation,
                        evidence=data.evidence, difficulty=data.difficulty, variant_of=data.variant_of)
    db.add(question)
    db.flush()
    question.validation = validate_question(db, question)
    audit(db, "question", question.id, user.id, "create", after=out(question))
    db.commit()
    return out(question)


@app.patch("/api/questions/{question_id}")
def edit_question(question_id: int, data: QuestionInput, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    question = require(db.get(Question, question_id))
    knowledge = require(db.get(Knowledge, data.knowledge_id))
    if not knowledge.chunk_id:
        raise HTTPException(400, "题目必须绑定教材原文")
    before = out(question)
    for key in ("knowledge_id", "stem", "options", "answer", "explanation", "evidence", "difficulty", "variant_of"):
        setattr(question, key, getattr(data, key))
    question.chunk_id, question.revision, question.status = knowledge.chunk_id, question.revision + 1, "draft"
    question.validation = validate_question(db, question)
    audit(db, "question", question.id, user.id, "edit", before, out(question))
    db.commit()
    return out(question)


@app.post("/api/questions/{question_id}/validate")
def revalidate(question_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    question = require(db.get(Question, question_id))
    question.validation = validate_question(db, question)
    db.commit()
    return out(question)


@app.post("/api/questions/{question_id}/review")
def review_question(question_id: int, data: ReviewInput, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    question = require(db.get(Question, question_id))
    before = out(question)
    question.validation = validate_question(db, question)
    if data.approve and question.validation:
        raise HTTPException(400, {"validation": question.validation})
    question.status, question.review_note = ("approved" if data.approve else "rejected"), data.note
    audit(db, "question", question.id, user.id, "review", before, out(question))
    db.commit()
    return out(question)


class PaperInput(BaseModel):
    book_id: int
    title: str
    knowledge_ids: list[int]
    question_count: int = Field(ge=1, le=100)
    score_each: int = Field(ge=1, le=100)
    difficulty: str = "any"


@app.post("/api/papers")
def create_paper(data: PaperInput, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    require(db.get(Book, data.book_id))
    allowed_ids = set(db.scalars(select(Knowledge.id).where(Knowledge.book_id == data.book_id, Knowledge.status == "approved")).all())
    if not data.knowledge_ids or not set(data.knowledge_ids).issubset(allowed_ids):
        raise HTTPException(400, "请选择本教材已审核知识点")
    pool = db.scalars(select(Question).where(Question.knowledge_id.in_(data.knowledge_ids), Question.status == "approved").order_by(Question.id)).all()
    if data.difficulty != "any":
        pool = [q for q in pool if q.difficulty == data.difficulty]
    selected = []
    # Select across knowledge points before taking a second question from one point.
    while len(selected) < data.question_count:
        progressed = False
        for knowledge_id in data.knowledge_ids:
            candidate = next((q for q in pool if q.knowledge_id == knowledge_id and q.id not in {x.id for x in selected}), None)
            if candidate:
                selected.append(candidate)
                progressed = True
                if len(selected) == data.question_count:
                    break
        if not progressed:
            raise HTTPException(400, "已审核题目数量不足")
    rule = {**data.model_dump(), "question_ids": [q.id for q in selected], "coverage": len({q.knowledge_id for q in selected}) / len(set(data.knowledge_ids))}
    paper = Paper(book_id=data.book_id, title=data.title, rule=rule,
                  total_score=data.question_count * data.score_each)
    db.add(paper)
    db.flush()
    audit(db, "paper", paper.id, user.id, "create", after=out(paper))
    db.commit()
    return out(paper)


@app.get("/api/papers")
def papers(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Paper).order_by(Paper.id.desc())
    if user.role != "admin":
        query = query.where(Paper.status == "published")
    result = out(db.scalars(query).all())
    if user.role != "admin":
        for paper in result:
            paper.pop("snapshot", None)
            paper.pop("rule", None)
    return result


@app.get("/api/papers/{paper_id}")
def paper_detail(paper_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    paper = require(db.get(Paper, paper_id))
    if user.role != "admin":
        if paper.status != "published":
            raise HTTPException(404, "试卷不存在")
        return {"id": paper.id, "title": paper.title, "total_score": paper.total_score,
                "starts_at": paper.starts_at, "ends_at": paper.ends_at, "status": paper.status}
    return out(paper)


class PublishInput(BaseModel):
    starts_at: datetime
    ends_at: datetime


@app.post("/api/papers/{paper_id}/publish")
def publish(paper_id: int, data: PublishInput, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    paper = require(db.get(Paper, paper_id))
    if paper.status != "draft":
        raise HTTPException(400, "试卷已经发布")
    start = data.starts_at.astimezone(timezone.utc).replace(tzinfo=None) if data.starts_at.tzinfo else data.starts_at
    end = data.ends_at.astimezone(timezone.utc).replace(tzinfo=None) if data.ends_at.tzinfo else data.ends_at
    if end <= start or end <= now():
        raise HTTPException(400, "考试结束时间无效")
    questions = [require(db.get(Question, qid), "试题不存在") for qid in paper.rule["question_ids"]]
    for question in questions:
        if question.status != "approved" or validate_question(db, question):
            raise HTTPException(400, f"试题 {question.id} 已变化或未通过审核")
    snapshot = [question_snapshot(q, paper.rule["score_each"]) for q in questions]
    if sum(q["score"] for q in snapshot) != paper.total_score:
        raise HTTPException(400, "总分与评分规则不一致")
    paper.snapshot, paper.starts_at, paper.ends_at = snapshot, start, end
    paper.published_at, paper.status = now(), "published"
    audit(db, "paper", paper.id, user.id, "publish", after={"snapshot": snapshot, "starts_at": str(start), "ends_at": str(end)})
    db.commit()
    return out(paper)


def grade(db: Session, attempt: Attempt, paper: Paper):
    if attempt.submitted_at:
        return
    result = []
    for item in paper.snapshot:
        answer = (attempt.answers or {}).get(str(item["id"]), "")
        correct = answer == item["answer"]
        result.append({"question_id": item["id"], "knowledge_id": item["knowledge_id"],
                       "answer": answer, "correct": correct, "score": item["score"] if correct else 0,
                       "correct_answer": item["answer"], "explanation": item["explanation"],
                       "evidence": item["evidence"], "stem": item["stem"], "options": item["options"]})
    attempt.result, attempt.score, attempt.submitted_at = result, sum(r["score"] for r in result), now()
    db.commit()


@app.post("/api/papers/{paper_id}/start")
def start_exam(paper_id: int, user: User = Depends(roles("student", "admin")), db: Session = Depends(get_db)):
    paper = require(db.get(Paper, paper_id))
    if paper.status != "published":
        raise HTTPException(400, "考试尚未发布")
    attempt = db.scalar(select(Attempt).where(Attempt.paper_id == paper_id, Attempt.user_id == user.id))
    if attempt:
        if not attempt.submitted_at and now() >= paper.ends_at:
            grade(db, attempt, paper)
        return attempt_view(attempt, paper)
    if not (paper.starts_at <= now() < paper.ends_at):
        raise HTTPException(400, "当前不在考试时间内")
    attempt = Attempt(paper_id=paper_id, user_id=user.id, answers={})
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        attempt = db.scalar(select(Attempt).where(Attempt.paper_id == paper_id, Attempt.user_id == user.id))
    return attempt_view(attempt, paper)


def attempt_view(attempt: Attempt, paper: Paper):
    if attempt.submitted_at:
        return {"id": attempt.id, "submitted": True, "score": attempt.score}
    return {"id": attempt.id, "submitted": False, "ends_at": paper.ends_at,
            "answers": attempt.answers, "questions": [question_public_from_snapshot(q) for q in paper.snapshot]}


def question_public_from_snapshot(q):
    return {key: q[key] for key in ("id", "stem", "options", "score", "knowledge_id")}


class AnswerInput(BaseModel):
    question_id: int
    answer: str


@app.put("/api/attempts/{attempt_id}/answer")
def save_answer(attempt_id: int, data: AnswerInput, user: User = Depends(roles("student", "admin")), db: Session = Depends(get_db)):
    attempt = require(db.scalar(select(Attempt).where(Attempt.id == attempt_id).with_for_update()))
    paper = db.get(Paper, attempt.paper_id)
    if attempt.user_id != user.id:
        raise HTTPException(403, "无权访问")
    if attempt.submitted_at or now() >= paper.ends_at:
        grade(db, attempt, paper)
        raise HTTPException(400, "考试已结束")
    question = next((q for q in paper.snapshot if q["id"] == data.question_id), None)
    if not question or data.answer not in question["options"]:
        raise HTTPException(400, "答案无效")
    attempt.answers = {**(attempt.answers or {}), str(data.question_id): data.answer}
    db.commit()
    return {"saved": True, "answers": attempt.answers}


@app.post("/api/attempts/{attempt_id}/submit")
def submit(attempt_id: int, user: User = Depends(roles("student", "admin")), db: Session = Depends(get_db)):
    attempt = require(db.scalar(select(Attempt).where(Attempt.id == attempt_id).with_for_update()))
    if attempt.user_id != user.id:
        raise HTTPException(403, "无权访问")
    grade(db, attempt, db.get(Paper, attempt.paper_id))
    return {"id": attempt.id, "score": attempt.score, "submitted_at": attempt.submitted_at}


@app.get("/api/attempts/{attempt_id}")
def attempt_detail(attempt_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    attempt = require(db.get(Attempt, attempt_id))
    if user.role != "admin" and attempt.user_id != user.id:
        raise HTTPException(403, "无权访问")
    paper = db.get(Paper, attempt.paper_id)
    if not attempt.submitted_at and now() >= paper.ends_at:
        grade(db, attempt, paper)
    if not attempt.submitted_at:
        return attempt_view(attempt, paper)
    return out(attempt)


@app.get("/api/my/attempts")
def my_attempts(user: User = Depends(roles("student", "admin")), db: Session = Depends(get_db)):
    attempts = db.scalars(select(Attempt).where(Attempt.user_id == user.id).order_by(Attempt.id.desc())).all()
    for attempt in attempts:
        paper = db.get(Paper, attempt.paper_id)
        if not attempt.submitted_at and now() >= paper.ends_at:
            grade(db, attempt, paper)
    return [{**out(a), "paper_title": db.get(Paper, a.paper_id).title} for a in attempts]


@app.get("/api/my/mastery")
def mastery(user: User = Depends(roles("student", "admin")), db: Session = Depends(get_db)):
    result = {}
    for attempt in db.scalars(select(Attempt).where(Attempt.user_id == user.id, Attempt.submitted_at.is_not(None))).all():
        for row in attempt.result:
            knowledge = db.get(Knowledge, row["knowledge_id"])
            item = result.setdefault(str(row["knowledge_id"]), {"title": knowledge.title if knowledge else f"知识点 #{row['knowledge_id']}", "correct": 0, "total": 0})
            item["total"] += 1
            item["correct"] += int(row["correct"])
    return result


@app.get("/api/my/variant-requests")
def variant_requests(user: User = Depends(roles("student", "admin")), db: Session = Depends(get_db)):
    jobs = db.scalars(select(Job).where(Job.key.like(f"variant:%:{user.id}")).order_by(Job.id.desc())).all()
    result = []
    for job in jobs:
        original_id = (job.payload or {}).get("variant_of")
        question = db.scalar(select(Question).where(Question.variant_of == original_id).order_by(Question.id.desc())) if original_id else None
        result.append({"original_id": original_id, "job_status": job.status, "error": job.error,
                       "question_status": question.status if question else None})
    return result


class PracticeInput(BaseModel):
    source_attempt_id: int
    question_id: int
    mode: str = "original"


@app.post("/api/practices")
def create_practice(data: PracticeInput, user: User = Depends(roles("student", "admin")), db: Session = Depends(get_db)):
    attempt = require(db.get(Attempt, data.source_attempt_id))
    if attempt.user_id != user.id or not attempt.submitted_at:
        raise HTTPException(403, "只能练习自己的已交卷错题")
    wrong = next((r for r in attempt.result if r["question_id"] == data.question_id and not r["correct"]), None)
    if not wrong:
        raise HTTPException(400, "该题不是错题")
    original = require(db.get(Question, data.question_id))
    if data.mode == "original":
        question = original
    elif data.mode == "variant":
        question = db.scalar(select(Question).where(Question.variant_of == original.id, Question.status == "approved").order_by(Question.id.desc()))
        if not question:
            job = enqueue(db, "question", original.knowledge_id,
                          f"variant:{original.id}:{user.id}", {"variant_of": original.id})
            return {"pending_review": True, "job": out(job)}
    else:
        raise HTTPException(400, "练习方式无效")
    if question.status != "approved":
        raise HTTPException(400, "题目待重新审核")
    practice = Practice(user_id=user.id, question_id=question.id, source_attempt_id=attempt.id, mode=data.mode)
    db.add(practice)
    db.commit()
    return {"practice": out(practice), "question": question_public(question)}


class PracticeAnswer(BaseModel):
    answer: str


@app.post("/api/practices/{practice_id}/answer")
def answer_practice(practice_id: int, data: PracticeAnswer, user: User = Depends(roles("student", "admin")), db: Session = Depends(get_db)):
    practice = require(db.get(Practice, practice_id))
    if practice.user_id != user.id:
        raise HTTPException(403, "无权访问")
    question = db.get(Question, practice.question_id)
    if practice.answer is None:
        if data.answer not in question.options:
            raise HTTPException(400, "答案无效")
        practice.answer, practice.correct = data.answer, data.answer == question.answer
        db.commit()
    return {"correct": practice.correct, "answer": question.answer,
            "explanation": question.explanation, "evidence": question.evidence}


@app.get("/api/audits")
def audits(entity: str, entity_id: int, user: User = Depends(roles("admin")), db: Session = Depends(get_db)):
    return out(db.scalars(select(Audit).where(Audit.entity == entity, Audit.entity_id == entity_id).order_by(Audit.id.desc())).all())
