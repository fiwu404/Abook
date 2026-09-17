from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, now


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(20))


class ModelProvider(Base):
    __tablename__ = "model_providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), unique=True)
    base_url: Mapped[str] = mapped_column(String(500))
    api_style: Mapped[str] = mapped_column(String(20))
    api_key_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=now)


class AISettings(Base):
    __tablename__ = "ai_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    text_provider_id: Mapped[int | None] = mapped_column(ForeignKey("model_providers.id"), nullable=True)
    text_model: Mapped[str] = mapped_column(String(200), default="")
    vision_provider_id: Mapped[int | None] = mapped_column(ForeignKey("model_providers.id"), nullable=True)
    vision_model: Mapped[str] = mapped_column(String(200), default="")


class Book(Base):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(80))
    sha256: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="uploaded")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    toc: Mapped[list] = mapped_column(JSON, default=list)
    mapping_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[object] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("title", "version"),)


class BookCatalog(Base):
    __tablename__ = "book_catalogs"
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), primary_key=True)
    toc_pages: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(20), default="pending")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    classified: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_revision: Mapped[int] = mapped_column(Integer, default=0)


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    pdf_page: Mapped[int] = mapped_column(Integer)
    printed_page: Mapped[str] = mapped_column(String(30), default="")
    chapter: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    blocks: Mapped[list] = mapped_column(JSON, default=list)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    revised: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("book_id", "pdf_page"),)


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    chapter: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text)
    bbox: Mapped[list] = mapped_column(JSON, default=list)
    previous: Mapped[str] = mapped_column(Text, default="")
    following: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Knowledge(Base):
    __tablename__ = "knowledge"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    chunk_id: Mapped[int | None] = mapped_column(ForeignKey("chunks.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    source_quote: Mapped[str] = mapped_column(Text, default="")
    chapter: Mapped[str] = mapped_column(String(200), default="")
    origin: Mapped[str] = mapped_column(String(20), default="ai")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    review_note: Mapped[str] = mapped_column(Text, default="")


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_id: Mapped[int] = mapped_column(ForeignKey("knowledge.id"), index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id"))
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[dict] = mapped_column(JSON)
    answer: Mapped[str] = mapped_column(String(1))
    explanation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    validation: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    review_note: Mapped[str] = mapped_column(Text, default="")
    variant_of: Mapped[int | None] = mapped_column(ForeignKey("questions.id"), nullable=True)


class Paper(Base):
    __tablename__ = "papers"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"))
    title: Mapped[str] = mapped_column(String(200))
    rule: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[list] = mapped_column(JSON, default=list)
    total_score: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    starts_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    started_at: Mapped[object] = mapped_column(DateTime, default=now)
    submitted_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[list] = mapped_column(JSON, default=list)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint("paper_id", "user_id"),)


class Practice(Base):
    __tablename__ = "practices"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    source_attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"))
    mode: Mapped[str] = mapped_column(String(20))
    answer: Mapped[str | None] = mapped_column(String(1), nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(150), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    celery_id: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[object] = mapped_column(DateTime, default=now)


class JobDismissal(Base):
    __tablename__ = "job_dismissals"
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[object] = mapped_column(DateTime, default=now)


class Audit(Base):
    __tablename__ = "audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime, default=now)
