"""Database-backed OpenAI-compatible text and vision adapters."""
import base64
import hashlib
import json
import time
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from .config import app_secret
from .db import SessionLocal
from .models import AISettings, Audit, ModelProvider


def validate_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError as exc:
        raise ValueError("模型地址格式无效") from exc
    if ("{" in url or "}" in url or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or url.endswith("/chat/completions")):
        raise ValueError("模型地址须为已填写完整变量的 http(s) API 根地址，例如 https://api.example.com/v1")
    return url


def _fernet() -> Fernet:
    secret = app_secret().encode()
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


def initialize_ai_settings(db) -> None:
    settings = db.get(AISettings, 1)
    if not settings:
        db.add(AISettings(id=1, text_provider_id=None, text_model="",
                          vision_provider_id=None, vision_model=""))
        db.commit()
        return
    provider = db.get(ModelProvider, settings.text_provider_id) if settings.text_provider_id else None
    legacy_seed = (
        provider
        and settings.text_provider_id == settings.vision_provider_id
        and provider.display_name in {"本机 Ollama", "默认云端模型"}
        and (settings.text_model, settings.vision_model) == ("qwen3-vl:8b", "qwen3-vl:4b")
        and not db.scalar(select(Audit.id).where(Audit.entity == "ai_config", Audit.entity_id == 1))
        and not db.scalar(select(Audit.id).where(Audit.entity == "provider", Audit.entity_id == provider.id))
    )
    if legacy_seed:
        settings.text_provider_id = settings.vision_provider_id = None
        settings.text_model = settings.vision_model = ""
        db.flush()
        db.delete(provider)
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
        headers = ({"x-api-key": key, "anthropic-version": "2023-06-01"} if provider.api_style == "anthropic"
                   else {"Authorization": "Bearer " + key} if key else {})
    with _client(20) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        body = response.json()
    entries = body.get("models", []) if provider.api_style == "ollama" else body.get("data", [])
    if not isinstance(entries, list):
        raise ValueError("提供商返回的模型列表格式无效")
    return sorted({str(item.get("name" if provider.api_style == "ollama" else "id"))
                   for item in entries if isinstance(item, dict) and item.get("name" if provider.api_style == "ollama" else "id")})


def test_connection(provider: ModelProvider) -> dict:
    start = time.monotonic()
    models = list_models(provider)
    return {"ok": True, "latency_ms": round((time.monotonic() - start) * 1000),
            "models": models, "model_count": len(models)}


def _anthropic_content(content):
    if isinstance(content, str):
        return content
    converted = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            converted.append({"type": "text", "text": str(part.get("text", ""))})
        elif part.get("type") == "image_url":
            url = str((part.get("image_url") or {}).get("url", ""))
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(",", 1)
                converted.append({"type": "image", "source": {
                    "type": "base64", "media_type": header[5:].split(";", 1)[0], "data": data}})
            elif url:
                converted.append({"type": "image", "source": {"type": "url", "url": url}})
    return converted


def _anthropic_payload(model: str, messages: list[dict]) -> dict:
    system = "\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "system")
    converted = [{"role": item.get("role", "user"), "content": _anthropic_content(item.get("content", ""))}
                 for item in messages if item.get("role") in {"user", "assistant"}]
    payload = {"model": model, "max_tokens": 4096, "messages": converted}
    if system:
        payload["system"] = system
    return payload


def _request(provider: ModelProvider, model: str, messages: list[dict], json_mode: bool = False,
             timeout: float = 300) -> str:
    if not model:
        raise RuntimeError("尚未选择模型")
    base = validate_base_url(provider.base_url)
    key = decrypt_key(provider)
    anthropic = provider.api_style == "anthropic"
    headers = ({"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
               if anthropic else {"Authorization": "Bearer " + key} if key else {})
    payload = _anthropic_payload(model, messages) if anthropic else {"model": model, "messages": messages}
    if json_mode and not anthropic:
        payload["response_format"] = {"type": "json_object"}
    with _client(timeout) as client:
        response = client.post(base + ("/messages" if anthropic else "/chat/completions"),
                               headers=headers, json=payload)
        # Ollama may return 500 when its JSON grammar parser rejects a vision
        # model's OCR-style reply. Retry once without the forced format.
        if json_mode and (response.status_code in {400, 422} or
                          provider.api_style == "ollama" and response.status_code == 500):
            payload.pop("response_format")
            response = client.post(base + "/chat/completions", headers=headers, json=payload)
        if response.is_error:
            try:
                detail = response.json().get("error", response.text)
            except (ValueError, AttributeError):
                detail = response.text
            raise RuntimeError(f"{provider.display_name} / {model} 返回 HTTP {response.status_code}：{str(detail)[:400]}")
        body = response.json()
        message = body if anthropic else body["choices"][0]["message"]
    content = message.get("content") or message.get("reasoning") or message.get("thinking")
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("模型未返回正文，请检查模型输出和推理配置")
    return content.strip()


def _json_object(raw: str) -> dict:
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        if start < 0:
            raise ValueError("模型没有返回 JSON 对象") from None
        try:
            value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        except json.JSONDecodeError as exc:
            raise ValueError("模型返回的 JSON 无法解析") from exc
    if not isinstance(value, dict):
        raise ValueError("模型没有返回 JSON 对象")
    return value


def _selected(kind: str) -> tuple[ModelProvider, str]:
    # Deliberately query the single settings row for every AI call. Provider
    # and model changes made in the admin page therefore take effect without
    # restarting the API or Celery worker.
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
    return _json_object(raw)


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
    try:
        return _json_object(raw)
    except ValueError:
        # A vision model may return the page transcription instead of the
        # requested JSON. The caller can still use visible headings from it.
        return {"raw_text": raw[:10000]}


def test_model(provider: ModelProvider, model: str, vision: bool = False) -> dict:
    if vision:
        # A tiny generated PNG exercises the image input path without transmitting textbook content.
        import pymupdf
        document = pymupdf.open()
        page = document.new_page(width=160, height=70)
        page.insert_text((12, 40), "TEST 123", fontsize=18)
        png = page.get_pixmap().tobytes("png")
        document.close()
        content = [{"type": "text", "text": "读出图中文字，只返回识别出的文字。"},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]
    else:
        content = "请只回复 OK。"
    start = time.monotonic()
    reply = _request(provider, model, [{"role": "user", "content": content}], timeout=60)
    return {"ok": True, "latency_ms": round((time.monotonic() - start) * 1000), "reply": reply[:160]}
