"""One real HTTP path through the manual fallback and reviewed exam flow."""
import io
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path

import httpx
import pymupdf
from fastapi.testclient import TestClient


tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(tmp.name) / "test.db")
os.environ["UPLOAD_DIR"] = str(Path(tmp.name) / "uploads")
os.environ["APP_SECRET"] = "test-secret-abcdefghijklmnopqrstuvwxyz-12345"
os.environ["ADMIN_PASSWORD"] = "admin12345678"

from app.db import now  # noqa: E402
from app.main import app  # noqa: E402
from app.tasks import celery_app  # noqa: E402
from app import ai  # noqa: E402


celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


def ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


def headers(client, username, password):
    token = ok(client.post("/api/auth/login", json={"username": username, "password": password}))["token"]
    return {"Authorization": "Bearer " + token}


def test_reviewed_exam_and_invalidation(monkeypatch):
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Photosynthesis converts sunlight into chemical energy in plants.")
    body = pdf.tobytes()
    pdf.close()
    with TestClient(app) as client:
        operator = headers(client, "admin", "admin12345678")
        reviewer = operator
        assert client.post("/api/auth/register", json={"username": "other", "password": "student1234"}).status_code == 404
        ok(client.post("/api/users", headers=operator, json={"username": "student_one", "password": "student1234", "role": "student"}))
        ok(client.post("/api/users", headers=operator, json={"username": "teacher_one", "password": "teacher1234", "role": "teacher"}))
        assert len(ok(client.get("/api/users", headers=operator))) == 3
        student = headers(client, "student_one", "student1234")
        teacher = headers(client, "teacher_one", "teacher1234")
        assert client.get("/api/users", headers=teacher).status_code == 403
        book = ok(client.post("/api/books?title=Biology&version=1", headers=operator,
                              files={"file": ("book.pdf", io.BytesIO(body), "application/pdf")}))
        assert client.post("/api/books?title=Other&version=1", headers=teacher,
                           files={"file": ("book.pdf", io.BytesIO(body), "application/pdf")}).status_code == 403
        assert book["version"] == "1"
        job = ok(client.post(f"/api/books/{book['id']}/parse", headers=operator))
        assert ok(client.get("/api/jobs", headers=operator))[0]["status"] == "done"
        pages = ok(client.get(f"/api/books/{book['id']}/pages", headers=operator))
        assert len(pages) == 1
        ok(client.post(f"/api/books/{book['id']}/confirm-mapping", headers=operator))
        section = ok(client.get(f"/api/books/{book['id']}/learning", headers=student))["sections"][0]
        assert ok(client.get(f"/api/books/{book['id']}/learning", headers=teacher))["sections"]
        quote = "Photosynthesis converts sunlight into chemical energy"
        knowledge = ok(client.post(f"/api/books/{book['id']}/knowledge", headers=operator, json={
            "title": "Photosynthesis", "content": "Plants convert sunlight to chemical energy.",
            "chunk_id": section["chunk_id"], "source_quote": quote, "chapter": "Unit 1"}))
        ok(client.post(f"/api/knowledge/{knowledge['id']}/review", headers=reviewer, json={"approve": True}))
        question_data = {"knowledge_id": knowledge["id"], "stem": "What energy is converted in photosynthesis?",
                         "options": {"A": "Sunlight", "B": "Sound", "C": "Heat", "D": "Motion"},
                         "answer": "A", "explanation": "The source names sunlight.", "evidence": quote,
                         "difficulty": "easy"}
        question = ok(client.post("/api/questions", headers=operator, json=question_data))
        assert question["validation"] == []
        ok(client.post(f"/api/questions/{question['id']}/review", headers=reviewer, json={"approve": True}))
        paper = ok(client.post("/api/papers", headers=operator, json={"book_id": book["id"],
            "title": "Unit quiz", "knowledge_ids": [knowledge["id"]], "question_count": 1,
            "score_each": 10, "difficulty": "any"}))
        start = now() - timedelta(minutes=1)
        end = now() + timedelta(minutes=30)
        ok(client.post(f"/api/papers/{paper['id']}/publish", headers=operator,
                       json={"starts_at": start.isoformat(), "ends_at": end.isoformat()}))
        assert "snapshot" not in ok(client.get(f"/api/papers/{paper['id']}", headers=teacher))
        assert client.post(f"/api/papers/{paper['id']}/start", headers=teacher).status_code == 403
        admin_attempt = ok(client.post(f"/api/papers/{paper['id']}/start", headers=operator))
        assert "answer" not in admin_attempt["questions"][0]
        ok(client.put(f"/api/attempts/{admin_attempt['id']}/answer", headers=operator,
                      json={"question_id": question["id"], "answer": "A"}))
        assert ok(client.post(f"/api/attempts/{admin_attempt['id']}/submit", headers=operator))["score"] == 10
        student_paper = ok(client.get(f"/api/papers/{paper['id']}", headers=student))
        assert "snapshot" not in student_paper
        attempt = ok(client.post(f"/api/papers/{paper['id']}/start", headers=student))
        assert "answer" not in attempt["questions"][0]
        ok(client.put(f"/api/attempts/{attempt['id']}/answer", headers=student,
                      json={"question_id": question["id"], "answer": "B"}))
        submitted = ok(client.post(f"/api/attempts/{attempt['id']}/submit", headers=student))
        assert submitted["score"] == 0
        assert ok(client.post(f"/api/attempts/{attempt['id']}/submit", headers=student))["score"] == 0
        practice = ok(client.post("/api/practices", headers=student, json={
            "source_attempt_id": attempt["id"], "question_id": question["id"], "mode": "original"}))
        assert practice["question"]["id"] == question["id"]
        feedback = ok(client.post(f"/api/practices/{practice['practice']['id']}/answer", headers=student, json={"answer": "A"}))
        assert feedback["correct"] is True
        monkeypatch.setattr("app.tasks.structured", lambda prompt: {
            "stem": "Which input starts the described process?",
            "options": {"A": "Sound", "B": "Sunlight", "C": "Motion", "D": "Pressure"},
            "answer": "B", "explanation": "The cited text names sunlight.",
            "evidence": quote, "difficulty": "easy"})
        pending = ok(client.post("/api/practices", headers=student, json={
            "source_attempt_id": attempt["id"], "question_id": question["id"], "mode": "variant"}))
        assert pending["pending_review"] is True
        variants = ok(client.get(f"/api/questions?book_id={book['id']}", headers=reviewer))
        variant = next(q for q in variants if q["variant_of"] == question["id"])
        assert variant["status"] == "draft"
        ok(client.post(f"/api/questions/{variant['id']}/review", headers=reviewer, json={"approve": True}))
        assert ok(client.get("/api/my/variant-requests", headers=student))[0]["question_status"] == "approved"
        variant_practice = ok(client.post("/api/practices", headers=student, json={
            "source_attempt_id": attempt["id"], "question_id": question["id"], "mode": "variant"}))
        assert variant_practice["question"]["id"] == variant["id"]
        page_data = {"printed_page": "1", "chapter": "Unit 1", "text": "Changed content.", "issues": []}
        ok(client.patch(f"/api/pages/{pages[0]['id']}", headers=operator, json=page_data))
        changed = ok(client.get(f"/api/books/{book['id']}/knowledge", headers=reviewer))[0]
        assert changed["status"] == "needs_review"
        old_exam = ok(client.get(f"/api/attempts/{attempt['id']}", headers=student))
        assert old_exam["result"][0]["evidence"] == quote


def test_cloud_provider_models_selection_and_vision(monkeypatch):
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers.get("Authorization") == "Bearer test-cloud-key"
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "cloud-vl"}, {"id": "cloud-text"}]})
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] in {"cloud-vl", "cloud-text"}
        if payload.get("response_format"):
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "TEST 123"}}]})

    monkeypatch.setattr(ai, "_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(respond), timeout=timeout))
    with TestClient(app) as client:
        admin = headers(client, "admin", "admin12345678")
        ok(client.post("/api/users", headers=admin, json={"username": "teacher_model", "password": "teacher1234", "role": "teacher"}))
        teacher = headers(client, "teacher_model", "teacher1234")
        assert client.get("/api/providers", headers=teacher).status_code == 403
        created = ok(client.post("/api/providers", headers=admin, json={
            "display_name": "云端视觉", "base_url": "https://example.test/v1", "api_style": "openai",
            "api_key": "test-cloud-key"}))
        assert created["has_api_key"] is True
        listed = ok(client.get("/api/providers", headers=admin))
        assert "test-cloud-key" not in str(listed) and "api_key_cipher" not in str(listed)
        assert ok(client.get(f"/api/providers/{created['id']}/models", headers=admin))["models"] == ["cloud-text", "cloud-vl"]
        assert ok(client.post(f"/api/providers/{created['id']}/test", headers=admin,
                              json={"model": "cloud-vl", "vision": True}))["ok"] is True
        config = ok(client.put("/api/ai/config", headers=admin, json={
            "text_provider_id": created["id"], "text_model": "cloud-text",
            "vision_provider_id": created["id"], "vision_model": "cloud-vl"}))
        assert config["vision_model"] == "cloud-vl"
        assert ai.structured("test") == {"ok": True}
        assert ai.ocr_png(b"png") == "TEST 123"
        assert any(b"image_url" in request.content for request in requests)
