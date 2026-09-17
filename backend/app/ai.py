"""Database-backed OpenAI-compatible text and vision adapters."""
import base64
import hashlib
import json
import os
import time
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from .db import SessionLocal
from .models import AISettings, ModelProvider


def validate_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError as exc:
        raise ValueError("模型地址格式无效") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or url.endswith("/chat/completions")):
        raise ValueError("模型地址须为 http(s) API 根地址，例如 https://api.example.com/v1")
    return url


def _fernet() -> Fernet:
    secret = os.environ["APP_SECRET"].encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def encrypt_key(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_key(provider: ModelProvider) -> str:
    if not provider.api_key_cipher:
        return "ollama" if provider.api_style == "ollama" else ""
    try:
        return _fernet().decrypt(provider.api_key_cipher.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("提供商密钥无法解密，请重新填写；APP_SECRET 可能已更换") from exc


def seed_ai_settings(db) -> None:
    settings = db.get(AISettings, 1)
    if settings:
        return
    base_url = validate_base_url(os.getenv("MODEL_BASE_URL") or "http://host.docker.internal:11434/v1")
    api_style = "ollama" if urlsplit(base_url).port == 11434 else "openai"
    name = "本机 Ollama" if api_style == "ollama" else "默认云端模型"
    provider = db.scalar(select(ModelProvider).where(ModelProvider.display_name == name))
    if not provider:
        key = os.getenv("MODEL_API_KEY", "")
        provider = ModelProvider(display_name=name, base_url=base_url, api_style=api_style,
                                 api_key_cipher=encrypt_key(key) if api_style == "openai" and key else None)
        db.add(provider)
        db.flush()
    db.add(AISettings(id=1, text_provider_id=provider.id, text_model=os.getenv("TEXT_MODEL") or "qwen3-vl:8b",
                      vision_provider_id=provider.id, vision_model=os.getenv("OCR_MODEL") or "qwen3-vl:4b"))
    db.commit()


def provider_view(provider: ModelProvider) -> dict:
    return {"id": provider.id, "display_name": provider.display_name, "base_url": provider.base_url,
            "api_style": provider.api_style, "has_api_key": bool(provider.api_key_cipher)}


def config_view(db) -> dict:
    settings = db.get(AISettings, 1)
    if not settings:
        return {"text_provider_id": None, "text_model": "", "vision_provider_id": None, "vision_model": ""}
    return {"text_provider_id": settings.text_provider_id, "text_model": settings.text_model,
            "vision_provider_id": settings.vision_provider_id, "vision_model": settings.vision_model}


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout)


def list_models(provider: ModelProvider) -> list[str]:
    base = validate_base_url(provider.base_url)
    if provider.api_style == "ollama":
        parsed = urlsplit(base)
        url = f"{parsed.scheme}://{parsed.netloc}/api/tags"
        headers = {}
    else:
        url = base + "/models"
        key = decrypt_key(provider)
        headers = {"Authorization": "Bearer " + key} if key else {}
    with _client(20) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        body = response.json()
    entries = body.get("models", []) if provider.api_style == "ollama" else body.get("data", [])
    if not isinstance(entries, list):
        raise ValueError("提供商返回的模型列表格式无效")
    return sorted({str(item.get("name" if provider.api_style == "ollama" else "id"))
                   for item in entries if isinstance(item, dict) and item.get("name" if provider.api_style == "ollama" else "id")})


def _request(provider: ModelProvider, model: str, messages: list[dict], json_mode: bool = False,
             timeout: float = 300) -> str:
    if not model:
        raise RuntimeError("尚未选择模型")
    base = validate_base_url(provider.base_url)
    key = decrypt_key(provider)
    headers = {"Authorization": "Bearer " + key} if key else {}
    payload = {"model": model, "messages": messages}
    if provider.api_style == "ollama" and os.getenv("MODEL_REASONING_EFFORT"):
        payload["reasoning_effort"] = os.environ["MODEL_REASONING_EFFORT"]
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    with _client(timeout) as client:
        response = client.post(base + "/chat/completions", headers=headers, json=payload)
        if json_mode and response.status_code in {400, 422}:
            payload.pop("response_format")
            response = client.post(base + "/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
    content = message.get("content") or message.get("reasoning") or message.get("thinking")
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("模型未返回正文，请检查模型输出和推理配置")
    return content.strip()


def _selected(kind: str) -> tuple[ModelProvider, str]:
    with SessionLocal() as db:
        settings = db.get(AISettings, 1)
        provider_id = getattr(settings, f"{kind}_provider_id", None) if settings else None
        model = getattr(settings, f"{kind}_model", "") if settings else ""
        provider = db.get(ModelProvider, provider_id) if provider_id else None
        if not provider or not model:
            raise RuntimeError("模型尚未配置，请由 admin 在模型接入页选择提供商和模型")
        db.expunge(provider)
        return provider, model


def structured(prompt: str) -> dict:
    provider, model = _selected("text")
    raw = _request(provider, model, [
        {"role": "system", "content": "你是严谨的教材编辑。只返回 JSON，不要 Markdown。所有依据必须逐字来自输入原文。"},
        {"role": "user", "content": prompt},
    ], json_mode=True)
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def ocr_png(png: bytes) -> str:
    provider, model = _selected("vision")
    encoded = base64.b64encode(png).decode()
    return _request(provider, model, [{"role": "user", "content": [
        {"type": "text", "text": "逐字识别这页教材，保留段落、公式和表格内容。只输出识别文本。"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
    ]}])


def vision_json(png: bytes, prompt: str) -> dict:
    provider, model = _selected("vision")
    encoded = base64.b64encode(png).decode()
    raw = _request(provider, model, [{"role": "user", "content": [
        {"type": "text", "text": prompt + " 只返回 JSON，不要 Markdown。"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
    ]}], json_mode=True)
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def test_model(provider: ModelProvider, model: str, vision: bool = False) -> dict:
    if vision:
        # A tiny generated PNG exercises the image input path without transmitting textbook content.
        import pymupdf
        document = pymupdf.open()
        page = document.new_page(width=160, height=70)
        page.insert_text((12, 40), "TEST 123", fontsize=18)
        png = page.get_pixmap().tobytes("png")
        document.close()
        content = [{"type": "text", "text": "请简短读出图中的文字。"},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]
    else:
        content = "请只回复 OK。"
    start = time.monotonic()
    reply = _request(provider, model, [{"role": "user", "content": content}], timeout=60)
    return {"ok": True, "latency_ms": round((time.monotonic() - start) * 1000), "reply": reply[:160]}
