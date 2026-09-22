"""One real HTTP path through the manual fallback and reviewed exam flow."""
import io
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pymupdf
from fastapi.testclient import TestClient


tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(tmp.name) / "test.db")
os.environ["UPLOAD_DIR"] = str(Path(tmp.name) / "uploads")
os.environ["APP_SECRET"] = "test-secret-abcdefghijklmnopqrstuvwxyz-12345"
os.environ["ADMIN_PASSWORD"] = "admin12345678"

from app.db import SessionLocal, now  # noqa: E402
from app.main import app, enqueue  # noqa: E402
from app.tasks import _knowledge_batches, _source_weight, _verbatim_quote, celery_app  # noqa: E402
from app import ai  # noqa: E402
from app.catalog import chapter_entries, expand_heading_indices, parse_toc_text  # noqa: E402
from app.models import BookCatalog, Job, JobDismissal, ModelProvider, Page, Paper  # noqa: E402


celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


def ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


def headers(client, username, password):
    token = ok(client.post("/api/auth/login", json={"username": username, "password": password}))["token"]
    return {"Authorization": "Bearer " + token}


def test_detailed_knowledge_batches_keep_heading_and_source():
    chunks = [SimpleNamespace(id=1, text="定义：资源池。\n组成：计算、存储。"),
              SimpleNamespace(id=2, text="原理：" + "按需分配。" * 450),
              SimpleNamespace(id=3, text="分类：公有云和私有云。")]
    batches = _knowledge_batches(chunks, {1: 4, 2: 4, 3: 5})
    assert len(batches) >= 3
    assert batches[-1][0] == 5
    assert all({4 if chunk.id < 3 else 5 for chunk, _ in group} == {heading}
               for heading, group in batches)
    assert all(sum(_source_weight(part) for _, part in group) <= 3200 for _, group in batches)
    assert _verbatim_quote("计算\n 资源池", "计算资源池") == "计算\n 资源池"
    assert _verbatim_quote("计算资源池", "虚构知识") == ""


def test_reviewed_exam_and_invalidation(monkeypatch):
    monkeypatch.setattr("app.tasks.vision_json", lambda png, prompt: {"chapter": "Unit 1", "confidence": "high"})
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
        book = ok(client.post("/api/books?title=Biology", headers=operator,
                              files={"file": ("book.pdf", io.BytesIO(body), "application/pdf")},
                              data={"manual_toc": "1|Unit 1|1"}))
        assert "version" not in book
        assert client.post("/api/books?title=Other", headers=teacher,
                           files={"file": ("book.pdf", io.BytesIO(body), "application/pdf")}).status_code == 403
        assert client.post("/api/books?title=Biology", headers=operator,
                           files={"file": ("book.pdf", io.BytesIO(body), "application/pdf")}).status_code == 409
        ok(client.post(f"/api/books/{book['id']}/confirm-toc", headers=operator))
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
        draft = ok(client.post("/api/papers", headers=operator, json={"book_id": book["id"],
            "title": "Discarded draft", "knowledge_ids": [knowledge["id"]], "question_count": 1,
            "score_each": 10, "difficulty": "any"}))
        assert next(p for p in ok(client.get("/api/papers", headers=operator)) if p["id"] == draft["id"])["can_delete"] is True
        assert client.delete(f"/api/papers/{draft['id']}", headers=teacher).status_code == 403
        assert ok(client.delete(f"/api/papers/{draft['id']}", headers=operator))["deleted"] is True
        assert all(p["id"] != draft["id"] for p in ok(client.get("/api/papers", headers=operator)))
        paper = ok(client.post("/api/papers", headers=operator, json={"book_id": book["id"],
            "title": "Unit quiz", "knowledge_ids": [knowledge["id"]], "question_count": 1,
            "score_each": 10, "difficulty": "any"}))
        start = now() - timedelta(minutes=1)
        end = now() + timedelta(minutes=30)
        ok(client.post(f"/api/papers/{paper['id']}/publish", headers=operator,
                       json={"starts_at": start.isoformat(), "ends_at": end.isoformat()}))
        assert next(p for p in ok(client.get("/api/papers", headers=operator)) if p["id"] == paper["id"])["can_delete"] is False
        assert client.delete(f"/api/papers/{paper['id']}", headers=operator).status_code == 400
        ended_paper = ok(client.post("/api/papers", headers=operator, json={"book_id": book["id"],
            "title": "Finished quiz", "knowledge_ids": [knowledge["id"]], "question_count": 1,
            "score_each": 10, "difficulty": "any"}))
        ok(client.post(f"/api/papers/{ended_paper['id']}/publish", headers=operator,
                       json={"starts_at": start.isoformat(), "ends_at": end.isoformat()}))
        with SessionLocal() as db:
            db.get(Paper, ended_paper["id"]).ends_at = now() - timedelta(seconds=1)
            db.commit()
        assert next(p for p in ok(client.get("/api/papers", headers=operator)) if p["id"] == ended_paper["id"])["can_delete"] is True
        assert ok(client.delete(f"/api/papers/{ended_paper['id']}", headers=operator))["deleted"] is True
        assert client.get(f"/api/papers/{ended_paper['id']}", headers=operator).status_code == 404
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
        without_evidence = ok(client.post("/api/questions", headers=operator, json={
            **question_data, "stem": "Which energy source do plants use?", "evidence": ""}))
        assert "依据必须逐字出现在原文内容块中" in without_evidence["validation"]
        assert ok(client.post(f"/api/questions/{without_evidence['id']}/review", headers=operator,
                              json={"approve": True}))["status"] == "approved"
        page_data = {"printed_page": "1", "chapter": "Unit 1", "text": "Changed content.", "issues": []}
        ok(client.patch(f"/api/pages/{pages[0]['id']}", headers=operator, json=page_data))
        changed = ok(client.get(f"/api/books/{book['id']}/knowledge", headers=reviewer))[0]
        assert changed["status"] == "needs_review"
        old_exam = ok(client.get(f"/api/attempts/{attempt['id']}", headers=student))
        assert old_exam["result"][0]["evidence"] == quote
        assert client.delete(f"/api/knowledge/{knowledge['id']}", headers=teacher).status_code == 403
        ok(client.delete(f"/api/knowledge/{knowledge['id']}", headers=operator))
        assert ok(client.get(f"/api/books/{book['id']}/knowledge", headers=operator)) == []
        assert ok(client.get(f"/api/questions?book_id={book['id']}", headers=operator)) == []
        assert client.post(f"/api/knowledge/{knowledge['id']}/review", headers=operator,
                           json={"approve": True}).status_code == 404
        assert ok(client.get(f"/api/attempts/{attempt['id']}", headers=student))["result"][0]["evidence"] == quote
        assert client.delete(f"/api/books/{book['id']}", headers=teacher).status_code == 403
        ok(client.delete(f"/api/books/{book['id']}", headers=operator))
        assert all(item["id"] != book["id"] for item in ok(client.get("/api/books", headers=operator)))
        assert client.get(f"/api/books/{book['id']}/learning", headers=operator).status_code == 404
        assert ok(client.get(f"/api/papers/{paper['id']}", headers=student))["status"] == "published"
        replacement = ok(client.post("/api/books?title=Biology", headers=operator,
                                     files={"file": ("book.pdf", io.BytesIO(body), "application/pdf")},
                                     data={"manual_toc": "1|Unit 1|1"}))
        assert replacement["id"] != book["id"]
        schedule = ok(client.get(f"/api/papers/{paper['id']}", headers=operator))
        changed_schedule = ok(client.patch(f"/api/papers/{paper['id']}/schedule", headers=operator,
                                           json={"starts_at": schedule["starts_at"],
                                                 "ends_at": (now() + timedelta(minutes=45)).isoformat()}))
        assert changed_schedule["snapshot"] == schedule["snapshot"]
        assert changed_schedule["ends_at"] != schedule["ends_at"]
        ok(client.post("/api/users", headers=operator, json={"username": "student_open", "password": "student1234",
                                                         "role": "student"}))
        open_student = headers(client, "student_open", "student1234")
        open_attempt = ok(client.post(f"/api/papers/{paper['id']}/start", headers=open_student))
        ok(client.put(f"/api/attempts/{open_attempt['id']}/answer", headers=open_student,
                      json={"question_id": question["id"], "answer": "A"}))
        extended = ok(client.patch(f"/api/papers/{paper['id']}/schedule", headers=operator,
                                   json={"starts_at": changed_schedule["starts_at"],
                                         "ends_at": (now() + timedelta(minutes=60)).isoformat()}))
        assert extended["ends_at"] != changed_schedule["ends_at"]
        assert client.patch(f"/api/papers/{paper['id']}/schedule", headers=operator,
                            json={"starts_at": (now() + timedelta(minutes=1)).isoformat(),
                                  "ends_at": (now() + timedelta(minutes=45)).isoformat()}).status_code == 400
        assert client.post(f"/api/papers/{paper['id']}/terminate", headers=teacher).status_code == 403
        terminated = ok(client.post(f"/api/papers/{paper['id']}/terminate", headers=operator))
        assert terminated["status"] == "terminated"
        assert next(p for p in ok(client.get("/api/papers", headers=operator)) if p["id"] == paper["id"])["can_delete"] is True
        assert ok(client.get(f"/api/attempts/{open_attempt['id']}", headers=open_student))["score"] == 10
        assert client.post(f"/api/papers/{paper['id']}/start", headers=open_student).status_code == 400
        assert client.patch(f"/api/papers/{paper['id']}/schedule", headers=operator,
                            json={"starts_at": schedule["starts_at"], "ends_at": schedule["ends_at"]}).status_code == 400
        assert ok(client.delete(f"/api/papers/{paper['id']}", headers=operator))["deleted"] is True
        assert all(item["id"] != paper["id"] for item in ok(client.get("/api/papers", headers=operator)))
        assert ok(client.get(f"/api/attempts/{open_attempt['id']}", headers=open_student))["score"] == 10


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


def test_granular_permissions_create_and_update_immediately():
    with TestClient(app) as client:
        admin = headers(client, "admin", "admin12345678")
        catalog = ok(client.get("/api/permissions", headers=admin))
        assert {"users.manage", "jobs.manage", "models.view", "audits.view"}.issubset(
            {item["code"] for item in catalog})

        manager = ok(client.post("/api/users", headers=admin, json={
            "username": "permission_manager", "password": "manager1234", "role": "teacher",
            "permissions": ["users.manage"]}))
        assert {"users.manage", "users.view"}.issubset(set(manager["permissions"]))
        manager_headers = headers(client, "permission_manager", "manager1234")
        assert client.get("/api/users", headers=manager_headers).status_code == 200

        child = ok(client.post("/api/users", headers=manager_headers, json={
            "username": "permission_child", "password": "student1234", "role": "student",
            "permissions": ["jobs.manage"]}))
        assert set(child["permissions"]) == {"jobs.manage", "jobs.view"}
        child_headers = headers(client, "permission_child", "student1234")
        assert client.get("/api/jobs", headers=child_headers).status_code == 200
        assert client.get("/api/providers", headers=child_headers).status_code == 403

        changed = ok(client.put(f"/api/users/{child['id']}/permissions", headers=manager_headers,
                                json={"permissions": ["models.view"]}))
        assert changed["permissions"] == ["models.view"]
        assert client.get("/api/providers", headers=child_headers).status_code == 200
        assert client.get("/api/jobs", headers=child_headers).status_code == 403
        assert client.post("/api/providers", headers=child_headers, json={
            "display_name": "forbidden", "base_url": "http://localhost:11434", "api_style": "ollama"}).status_code == 403
        assert client.put(f"/api/users/{child['id']}/permissions", headers=manager_headers,
                          json={"permissions": ["unknown.permission"]}).status_code == 400

        admin_id = ok(client.get("/api/auth/me", headers=admin))["id"]
        assert client.put(f"/api/users/{admin_id}/permissions", headers=admin,
                          json={"permissions": []}).status_code == 400


def test_account_rename_password_reset_and_required_change():
    with TestClient(app) as client:
        admin = headers(client, "admin", "admin12345678")
        account = ok(client.post("/api/users", headers=admin, json={
            "username": "lifecycle_user", "password": "initial1234", "role": "student",
            "permissions": ["jobs.view"], "must_change_password": True}))
        assert account["must_change_password"] is True

        first_login = ok(client.post("/api/auth/login", json={
            "username": "lifecycle_user", "password": "initial1234"}))
        forced_headers = {"Authorization": "Bearer " + first_login["token"]}
        assert first_login["user"]["must_change_password"] is True
        assert ok(client.get("/api/auth/me", headers=forced_headers))["username"] == "lifecycle_user"
        assert client.get("/api/jobs", headers=forced_headers).status_code == 403
        assert client.put("/api/auth/password", headers=forced_headers, json={
            "current_password": "incorrect", "new_password": "personal1234"}).status_code == 400

        changed = ok(client.put("/api/auth/password", headers=forced_headers, json={
            "current_password": "initial1234", "new_password": "personal1234"}))
        personal_headers = {"Authorization": "Bearer " + changed["token"]}
        assert changed["user"]["must_change_password"] is False
        assert client.get("/api/auth/me", headers=forced_headers).status_code == 401
        assert client.get("/api/jobs", headers=personal_headers).status_code == 200

        renamed = ok(client.put(f"/api/users/{account['id']}", headers=admin,
                                json={"username": "lifecycle_renamed"}))
        assert renamed["username"] == "lifecycle_renamed"
        assert client.post("/api/auth/login", json={
            "username": "lifecycle_user", "password": "personal1234"}).status_code == 401
        renamed_login = ok(client.post("/api/auth/login", json={
            "username": "lifecycle_renamed", "password": "personal1234"}))
        renamed_headers = {"Authorization": "Bearer " + renamed_login["token"]}

        reset = ok(client.post(f"/api/users/{account['id']}/reset-password", headers=admin, json={
            "password": "restored1234", "must_change_password": True}))
        assert reset["must_change_password"] is True
        assert client.get("/api/auth/me", headers=renamed_headers).status_code == 401
        assert client.post("/api/auth/login", json={
            "username": "lifecycle_renamed", "password": "personal1234"}).status_code == 401
        restored_login = ok(client.post("/api/auth/login", json={
            "username": "lifecycle_renamed", "password": "restored1234"}))
        assert restored_login["user"]["must_change_password"] is True

        manager = ok(client.post("/api/users", headers=admin, json={
            "username": "lifecycle_manager", "password": "manager1234", "role": "teacher",
            "permissions": ["users.manage"]}))
        manager_headers = headers(client, "lifecycle_manager", "manager1234")
        assert client.post(f"/api/users/{manager['id']}/reset-password", headers=manager_headers, json={
            "password": "replacement1234", "must_change_password": True}).status_code == 400


def test_ollama_vision_json_parser_failure_falls_back(monkeypatch):
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("response_format"):
            return httpx.Response(500, json={"error": "JSON grammar rejected vision output"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "项目2 CentOS Linux操作系统安装\n学习目标"}}]})

    monkeypatch.setattr(ai, "_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(respond), timeout=timeout))
    provider = ModelProvider(display_name="本机 Ollama", base_url="http://127.0.0.1:11434/v1", api_style="ollama")
    assert ai._request(provider, "qwen3-vl:4b", [{"role": "user", "content": "classify"}], json_mode=True).startswith("项目2")
    assert len(requests) == 2 and "response_format" not in requests[1]

    from app.tasks import _visual_chapter
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "项目2 CentOS Linux操作系统安装")
    toc = [{"level": 1, "title": "项目1 云计算", "pdf_page": 1},
           {"level": 1, "title": "项目2 CentOS Linux操作系统安装", "pdf_page": 1}]
    monkeypatch.setattr("app.tasks.vision_json", lambda png, prompt: {"raw_text": "项目2\nCentOS Linux操作系统安装\n学习目标"})
    chapter, issues = _visual_chapter(document, 0, toc, {1})
    assert chapter == "项目2 CentOS Linux操作系统安装" and "页面文字" in issues[0]
    document.close()


def test_ollama_ocr_model_test_accepts_plain_text(monkeypatch):
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        assert "response_format" not in payload
        return httpx.Response(200, json={"choices": [{"message": {"content": "TEST 123"}}]})

    monkeypatch.setattr(ai, "_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(respond), timeout=timeout))
    provider = ModelProvider(display_name="本机 Ollama", base_url="http://127.0.0.1:11434/v1", api_style="ollama")
    result = ai.test_model(provider, "qwen3-vl:4b", vision=True)
    assert result["ok"] is True and result["reply"] == "TEST 123"
    assert len(requests) == 1
    assert requests[0]["messages"][0]["content"][1]["type"] == "image_url"


def test_directory_first_and_on_demand_chapter_workflow(monkeypatch):
    assert parse_toc_text("目录 CONTENTS\n项目1\n初识云计算与OpenStack 云计算平台........2")[0] == {
        "level": 1, "title": "项目1 初识云计算与OpenStack 云计算平台", "printed_page": 2}
    document = pymupdf.open()
    for lines in [
        ["Cloud textbook cover"],
        ["CONTENTS", "Chapter 1 Cloud Basics........................1", "1.1 Introduction.............................1", "1.2 Concepts.................................2"],
        ["Directory continued", "Chapter 2 Storage Basics......................3", "2.1 Storage types............................3", "2.2 Storage services.........................4"],
        ["Chapter 1 Cloud Basics", "Cloud converts compute resources into services."],
        ["Cloud services can be shared by several users."],
        ["Chapter 2 Storage Basics", "Storage keeps data for later use."],
    ]:
        page = document.new_page()
        for index, line in enumerate(lines):
            page.insert_text((72, 72 + index * 24), line)
    body = document.tobytes()
    document.close()
    calls = []

    def classify(png, prompt):
        calls.append(prompt)
        expected = prompt.split("目录推定 ", 1)[1].split("。", 1)[0]
        return {"chapter": expected, "confidence": "high"}

    monkeypatch.setattr("app.tasks.vision_json", classify)
    monkeypatch.setattr("app.tasks.ocr_png", lambda png: "前置内容")
    with TestClient(app) as client:
        admin = headers(client, "admin", "admin12345678")
        book = ok(client.post("/api/books?title=Cloud%20Guide", headers=admin,
                              files={"file": ("cloud.pdf", io.BytesIO(body), "application/pdf")}))
        assert not book["toc"]
        # Simulate a textbook imported by the old page-first parser.
        with SessionLocal() as db:
            db.delete(db.get(BookCatalog, book["id"]))
            for number in range(1, 7):
                db.add(Page(book_id=book["id"], pdf_page=number, printed_page=str(number),
                            chapter="未分章", text="old page text", blocks=[{"text": "old page text", "bbox": []}], issues=[]))
            db.commit()
        assert client.post(f"/api/books/{book['id']}/parse", headers=admin).status_code == 400
        job = ok(client.post(f"/api/books/{book['id']}/discover-toc", headers=admin))
        assert job["status"] == "done"
        catalog = ok(client.get(f"/api/books/{book['id']}/catalog", headers=admin))
        assert [item["pdf_page"] for item in catalog["chapters"]] == [4, 6]
        assert catalog["toc_pages"] == [2, 3]
        assert [item["title"] for item in catalog["chapters"]] == ["Chapter 1 Cloud Basics", "Chapter 2 Storage Basics"]
        assert [item["level"] for item in catalog["toc"]] == [1, 2, 2, 1, 2, 2]
        assert catalog["confirmed"] is False
        ok(client.post(f"/api/books/{book['id']}/confirm-toc", headers=admin))
        parsed = ok(client.post(f"/api/books/{book['id']}/parse", headers=admin))
        assert parsed["status"] == "done"
        assert calls, "chapter boundaries must be checked by the vision model"
        pages = ok(client.get(f"/api/books/{book['id']}/pages", headers=admin))
        assert [page["chapter"] for page in pages] == ["前置内容", "前置内容", "前置内容",
            "Chapter 1 Cloud Basics", "Chapter 1 Cloud Basics", "Chapter 2 Storage Basics"]
        ok(client.post(f"/api/books/{book['id']}/confirm-mapping", headers=admin))
        learning = ok(client.get(f"/api/books/{book['id']}/learning", headers=admin))
        first = next(section for section in learning["sections"] if "Cloud converts" in section["text"])
        quote = "Cloud converts compute resources into services."
        monkeypatch.setattr("app.tasks.structured", lambda prompt: {"items": [{"chunk_id": first["chunk_id"],
            "title": "Cloud services", "content": "Compute is offered as a service.", "source_quote": quote}]})
        knowledge_job = ok(client.post(f"/api/books/{book['id']}/extract-knowledge", headers=admin,
                                       json={"chapter": "Chapter 1 Cloud Basics"}))
        assert knowledge_job["status"] == "done"
        knowledge = ok(client.get(f"/api/books/{book['id']}/knowledge", headers=admin))
        assert len(knowledge) == 1 and knowledge[0]["chapter"] == "Chapter 1 Cloud Basics"
        ok(client.post(f"/api/knowledge/{knowledge[0]['id']}/review", headers=admin, json={"approve": True}))
        question_calls = []

        def generate_question(prompt):
            question_calls.append(prompt)
            return {"stem": "What does cloud computing offer?",
                    "options": {"A": "Compute services", "B": "Paper", "C": "Ink", "D": "Coal"},
                    "answer": "A", "explanation": "The chapter describes compute resources as services.",
                    "evidence": "Compute resources are provided as services." if len(question_calls) == 1 else quote,
                    "difficulty": "easy"}

        monkeypatch.setattr("app.tasks.structured", generate_question)
        question_job = ok(client.post(f"/api/books/{book['id']}/generate-chapter-questions", headers=admin,
                                       json={"chapter": "Chapter 1 Cloud Basics", "count": 1,
                                             "selected_heading_indices": [0]}))
        assert question_job["status"] == "done"
        questions = ok(client.get(f"/api/questions?book_id={book['id']}", headers=admin))
        assert len(questions) == 1 and questions[0]["validation"] == []
        assert len(question_calls) == 2
        assert "上一次生成的依据不是教材原文" in question_calls[1]
        monkeypatch.setattr("app.tasks.structured", lambda prompt: {
            "stem": "What does cloud computing offer? Please explain.",
            "options": {"A": "Compute services", "B": "Paper", "C": "Ink", "D": "Coal"},
            "answer": "A", "explanation": "The chapter describes compute resources as services.",
            "evidence": "The cloud automatically offers all services.", "difficulty": "easy"})
        invalid_job = ok(client.post(f"/api/knowledge/{knowledge[0]['id']}/generate-question", headers=admin))
        assert invalid_job["status"] == "done"
        invalid = ok(client.get(f"/api/questions?book_id={book['id']}", headers=admin))[0]
        assert "依据必须逐字出现在原文内容块中" in invalid["validation"]
        reviewed_invalid = ok(client.post(f"/api/questions/{invalid['id']}/review", headers=admin,
                                          json={"approve": True, "note": "已人工核对题干与答案"}))
        assert reviewed_invalid["status"] == "approved"
        assert "人工确认原文提示" in reviewed_invalid["review_note"]
        prompts = []

        def detailed(prompt):
            prompts.append(prompt)
            return {"items": [{"chunk_id": first["chunk_id"], "title": f"Cloud detail {index}",
                              "content": "Compute is offered as a service.", "source_quote": quote}
                             for index in range(14)]}

        monkeypatch.setattr("app.tasks.structured", detailed)
        detailed_job = ok(client.post(f"/api/books/{book['id']}/extract-knowledge", headers=admin,
                                      json={"chapter": "Chapter 1 Cloud Basics"}))
        assert detailed_job["status"] == "done"
        assert len(ok(client.get(f"/api/books/{book['id']}/knowledge", headers=admin))) == 15
        assert prompts and "逐段阅读原文" in prompts[0]
        assert client.post(f"/api/books/{book['id']}/extract-knowledge", headers=admin,
                           json={"chapter": "前置内容"}).status_code == 400


def test_textbook_library_and_heading_scoped_questions(monkeypatch):
    assert [item["title"] for item in chapter_entries([
        {"level": 1, "title": "项目1 网络", "pdf_page": 1},
        {"level": 2, "title": "任务1 配置设备", "pdf_page": 1},
    ])] == ["项目1 网络"]
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Chapter 7 Networks\n7.1 Network models\n7.1.1 Layered model\n"
                     "Layered networks separate responsibilities.\n7.2 Network devices\nSwitches connect local devices.")
    body = document.tobytes()
    document.close()
    monkeypatch.setattr("app.tasks.vision_json", lambda png, prompt: {"chapter": "Chapter 7 Networks", "confidence": "high"})
    toc = "1|Chapter 7 Networks|1\n2|7.1 Network models|1\n3|7.1.1 Layered model|1\n2|7.2 Network devices|1"
    with TestClient(app) as client:
        admin = headers(client, "admin", "admin12345678")
        book = ok(client.post("/api/books?title=Network%20Textbook", headers=admin,
                              files={"file": ("networks.pdf", io.BytesIO(body), "application/pdf")},
                              data={"manual_toc": toc}))
        assert any(item["id"] == book["id"] for item in ok(client.get("/api/books", headers=admin)))
        catalog = ok(client.get(f"/api/books/{book['id']}/catalog", headers=admin))
        assert [item["level"] for item in catalog["toc"]] == [1, 2, 3, 2]
        assert expand_heading_indices(catalog["toc"], "Chapter 7 Networks", [1]) == {1, 2}
        ok(client.post(f"/api/books/{book['id']}/confirm-toc", headers=admin))
        ok(client.post(f"/api/books/{book['id']}/parse", headers=admin))
        ok(client.post(f"/api/books/{book['id']}/confirm-mapping", headers=admin))
        sections = ok(client.get(f"/api/books/{book['id']}/learning", headers=admin))["sections"]
        first = next(item for item in sections if "Layered networks" in item["text"])
        second = next(item for item in sections if "Switches connect" in item["text"])
        created_knowledge = []
        for chunk, title, quote in [(first, "Layers", "Layered networks separate responsibilities."),
                                    (first, "Layer responsibilities", "Layered networks separate responsibilities."),
                                    (second, "Switches", "Switches connect local devices.")]:
            knowledge = ok(client.post(f"/api/books/{book['id']}/knowledge", headers=admin, json={
                "title": title, "content": quote, "chunk_id": chunk["chunk_id"],
                "source_quote": quote, "chapter": "Chapter 7 Networks"}))
            created_knowledge.append(knowledge)
            ok(client.post(f"/api/knowledge/{knowledge['id']}/review", headers=admin, json={"approve": True}))
        bad = ok(client.post("/api/questions", headers=admin, json={
            "knowledge_id": created_knowledge[0]["id"], "stem": "Which devices connect?",
            "options": {"A": "Switches", "B": "Routers", "C": "Books", "D": "Plants"},
            "answer": "A", "explanation": "The device statement is on the same page.",
            "evidence": "Switches connect local devices.", "difficulty": "easy"}))
        assert "依据必须逐字出现在知识点的原文摘录中" in bad["validation"]
        reviewed = ok(client.post(f"/api/questions/{bad['id']}/review", headers=admin,
                                  json={"approve": True, "note": "人工核对后接受引用"}))
        assert reviewed["status"] == "approved"
        assert reviewed["validation"] == bad["validation"]
        assert "人工核对后接受引用" in reviewed["review_note"]
        structurally_bad = ok(client.post("/api/questions", headers=admin, json={
            "knowledge_id": created_knowledge[0]["id"], "stem": "",
            "options": {"A": "One", "B": "Two", "C": "Three", "D": "Four"},
            "answer": "A", "explanation": "Manual check", "evidence": "", "difficulty": "easy"}))
        assert client.post(f"/api/questions/{structurally_bad['id']}/review", headers=admin,
                           json={"approve": True}).status_code == 400
        exact_rule = {"book_id": book["id"], "title": "Selected question", "chapter": "Chapter 7 Networks",
                      "question_ids": [bad["id"]], "question_count": 1, "score_each": 10, "difficulty": "easy"}
        exact_paper = ok(client.post("/api/papers", headers=admin, json=exact_rule))
        assert exact_paper["rule"]["question_ids"] == [bad["id"]]
        assert exact_paper["rule"]["knowledge_ids"] == [created_knowledge[0]["id"]]
        assert exact_paper["total_score"] == 10
        assert client.post("/api/papers", headers=admin, json={**exact_rule,
                           "question_ids": [structurally_bad["id"]]}).status_code == 400
        assert client.post("/api/papers", headers=admin, json={**exact_rule,
                           "question_ids": [bad["id"], bad["id"]], "question_count": 2}).status_code == 400
        assert client.post("/api/papers", headers=admin, json={**exact_rule,
                           "difficulty": "hard"}).status_code == 400
        paper = ok(client.post("/api/papers", headers=admin, json={
            "book_id": book["id"], "title": "Manual source review", "chapter": "Chapter 7 Networks",
            "knowledge_ids": [created_knowledge[0]["id"]], "question_count": 1,
            "score_each": 10, "difficulty": "any"}))
        start = now() - timedelta(minutes=1)
        end = now() + timedelta(minutes=30)
        published = ok(client.post(f"/api/papers/{paper['id']}/publish", headers=admin,
                                   json={"starts_at": start.isoformat(), "ends_at": end.isoformat()}))
        assert published["status"] == "published"
        generated_prompts = []

        def generate(prompt):
            generated_prompts.append(prompt)
            quote = "Switches connect local devices." if "Switches connect local devices." in prompt else "Layered networks separate responsibilities."
            return {"stem": f"Which fact is in the textbook: {quote}",
                    "options": {"A": quote, "B": "Other", "C": "Another", "D": "None"},
                    "answer": "A", "explanation": "The original text states this fact.",
                    "evidence": quote, "difficulty": "easy"}
        monkeypatch.setattr("app.tasks.structured", generate)
        assert client.post(f"/api/books/{book['id']}/generate-chapter-questions", headers=admin,
                           json={"chapter": "Chapter 7 Networks", "count": 1,
                                 "selected_heading_indices": [99]}).status_code == 400
        job = ok(client.post(f"/api/books/{book['id']}/generate-chapter-questions", headers=admin,
                             json={"chapter": "Chapter 7 Networks", "count": 1,
                                   "selected_heading_indices": [1]}))
        assert job["status"] == "done"
        questions = ok(client.get(f"/api/questions?book_id={book['id']}", headers=admin))
        assert any(item["evidence"] == "Layered networks separate responsibilities." for item in questions)
        job = ok(client.post(f"/api/books/{book['id']}/generate-chapter-questions", headers=admin,
                             json={"chapter": "Chapter 7 Networks", "count": 1,
                                   "selected_heading_indices": [3]}))
        assert job["status"] == "done"
        questions = ok(client.get(f"/api/questions?book_id={book['id']}", headers=admin))
        assert {item["evidence"] for item in questions if item["id"] not in {bad["id"], structurally_bad["id"]}} == {
            "Layered networks separate responsibilities.", "Switches connect local devices."}
        earlier_ids = {item["id"] for item in questions}
        chapter_job = ok(client.post(f"/api/books/{book['id']}/generate-chapter-questions", headers=admin,
                                     json={"chapter": "Chapter 7 Networks", "count": 2,
                                           "selected_heading_indices": [0]}))
        assert chapter_job["status"] == "done"
        chapter_questions = [item for item in ok(client.get(f"/api/questions?book_id={book['id']}", headers=admin))
                             if item["id"] not in earlier_ids]
        assert len(chapter_questions) == 2
        assert {item["knowledge_id"] for item in chapter_questions} & {created_knowledge[2]["id"]}
        assert {item["knowledge_id"] for item in chapter_questions} & {
            created_knowledge[0]["id"], created_knowledge[1]["id"]}
        assert any("所选目录标题：Chapter 7 Networks、7.2 Network devices" in prompt
                   for prompt in generated_prompts)


def test_admin_can_clear_finished_job_list_without_losing_task_records(monkeypatch):
    with TestClient(app) as client:
        admin = headers(client, "admin", "admin12345678")
        ok(client.post("/api/users", headers=admin, json={
            "username": "teacher_cleanup", "password": "teacher1234", "role": "teacher"}))
        teacher = headers(client, "teacher_cleanup", "teacher1234")
        with SessionLocal() as db:
            jobs = [Job(key=f"cleanup:{status}", kind="parse", target_id=0, status=status)
                    for status in ("done", "failed", "cancelled", "queued", "running", "retrying")]
            db.add_all(jobs)
            db.commit()
            ids = {job.status: job.id for job in jobs}
        assert client.delete("/api/jobs", headers=teacher).status_code == 403
        assert ok(client.delete("/api/jobs", headers=admin))["cleared"] >= 3
        visible = {job["id"] for job in ok(client.get("/api/jobs", headers=admin))}
        assert all(ids[status] not in visible for status in ("done", "failed", "cancelled"))
        assert all(ids[status] in visible for status in ("queued", "running", "retrying"))
        assert ok(client.delete("/api/jobs", headers=admin))["cleared"] == 0
        with SessionLocal() as db:
            assert db.get(Job, ids["done"]).status == "done"
            assert db.get(JobDismissal, ids["done"]).actor_id is not None
            assert db.get(JobDismissal, ids["running"]) is None
            db.get(Job, ids["running"]).status = "done"
            db.commit()
        assert ids["running"] in {job["id"] for job in ok(client.get("/api/jobs", headers=admin))}
        assert ok(client.delete("/api/jobs", headers=admin))["cleared"] == 1
        monkeypatch.setattr("app.main.run_job.apply_async", lambda args: SimpleNamespace(id="cleanup-retry"))
        retried = ok(client.post(f"/api/jobs/{ids['failed']}/retry", headers=admin))
        assert retried["status"] == "queued"
        assert ids["failed"] in {job["id"] for job in ok(client.get("/api/jobs", headers=admin))}
        with SessionLocal() as db:
            assert enqueue(db, "parse", 0, "cleanup:done").id == ids["done"]
        assert ids["done"] in {job["id"] for job in ok(client.get("/api/jobs", headers=admin))}


def test_figure_question_shows_original_pdf_page_in_review_and_exam(monkeypatch):
    monkeypatch.setattr("app.tasks.vision_json", lambda png, prompt: {"chapter": "Figures", "confidence": "high"})
    document = pymupdf.open()
    page = document.new_page(width=320, height=220)
    page.draw_rect(pymupdf.Rect(72, 95, 230, 150), color=(0, 0, 0), fill=(0.7, 0.85, 1))
    page.insert_text((72, 70), "A network diagram shows three nodes.")
    body = document.tobytes()
    document.close()
    with TestClient(app) as client:
        admin = headers(client, "admin", "admin12345678")
        ok(client.post("/api/users", headers=admin, json={
            "username": "student_figure", "password": "student1234", "role": "student"}))
        student = headers(client, "student_figure", "student1234")
        book = ok(client.post("/api/books?title=Figure%20Textbook", headers=admin,
                              files={"file": ("figures.pdf", io.BytesIO(body), "application/pdf")},
                              data={"manual_toc": "1|Figures|1"}))
        ok(client.post(f"/api/books/{book['id']}/confirm-toc", headers=admin))
        ok(client.post(f"/api/books/{book['id']}/parse", headers=admin))
        ok(client.post(f"/api/books/{book['id']}/confirm-mapping", headers=admin))
        chunk_id = ok(client.get(f"/api/books/{book['id']}/learning", headers=admin))["sections"][0]["chunk_id"]
        knowledge = ok(client.post(f"/api/books/{book['id']}/knowledge", headers=admin, json={
            "title": "Diagram", "content": "Three nodes are shown.", "chunk_id": chunk_id,
            "source_quote": "A network diagram shows three nodes.", "chapter": "Figures"}))
        ok(client.post(f"/api/knowledge/{knowledge['id']}/review", headers=admin, json={"approve": True}))
        question = ok(client.post("/api/questions", headers=admin, json={
            "knowledge_id": knowledge["id"], "stem": "图6-3 展示了什么？",
            "options": {"A": "Three nodes", "B": "A router", "C": "A cloud", "D": "A server"},
            "answer": "A", "explanation": "The source names three nodes.",
            "evidence": "A network diagram shows three nodes.", "difficulty": "easy"}))
        ok(client.post(f"/api/questions/{question['id']}/review", headers=admin, json={"approve": True}))
        reviewed = ok(client.get(f"/api/questions?book_id={book['id']}", headers=admin))
        assert reviewed[0]["image_pdf_page"] == 1
        full_page = client.get(f"/api/books/{book['id']}/pages/1/image", headers=student)
        cropped = client.get(
            f"/api/books/{book['id']}/pages/1/chunks/{chunk_id}/image", headers=student)
        assert full_page.content.startswith(b"\x89PNG") and cropped.content.startswith(b"\x89PNG")
        full_pixmap, crop_pixmap = pymupdf.Pixmap(full_page.content), pymupdf.Pixmap(cropped.content)
        assert crop_pixmap.width < full_pixmap.width and crop_pixmap.height < full_pixmap.height
        paper = ok(client.post("/api/papers", headers=admin, json={
            "book_id": book["id"], "title": "Figure exam", "chapter": "Figures",
            "question_ids": [question["id"]], "question_count": 1,
            "score_each": 10, "difficulty": "easy"}))
        start = now() - timedelta(minutes=1)
        end = now() + timedelta(minutes=30)
        published = ok(client.post(f"/api/papers/{paper['id']}/publish", headers=admin,
                                   json={"starts_at": start.isoformat(), "ends_at": end.isoformat()}))
        assert published["snapshot"][0]["image_pdf_page"] == 1
        with SessionLocal() as db:
            saved = db.get(Paper, paper["id"])
            saved.snapshot = [{key: value for key, value in item.items() if key != "image_pdf_page"}
                              for item in saved.snapshot]
            db.commit()
        attempt = ok(client.post(f"/api/papers/{paper['id']}/start", headers=student))
        assert attempt["book_id"] == book["id"] and attempt["questions"][0]["image_pdf_page"] == 1
        assert attempt["questions"][0]["image_chunk_id"] == chunk_id
        ok(client.put(f"/api/attempts/{attempt['id']}/answer", headers=student,
                      json={"question_id": question["id"], "answer": "B"}))
        ok(client.post(f"/api/attempts/{attempt['id']}/submit", headers=student))
        result = ok(client.get(f"/api/attempts/{attempt['id']}", headers=student))
        assert result["result"][0]["image_pdf_page"] == 1
        assert result["result"][0]["image_chunk_id"] == chunk_id
        ok(client.delete(f"/api/books/{book['id']}", headers=admin))
        assert client.get(f"/api/books/{book['id']}/pages/1/image", headers=student).content.startswith(b"\x89PNG")
