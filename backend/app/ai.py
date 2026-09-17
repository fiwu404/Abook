"""OpenAI-compatible text and vision adapter for cloud endpoints or local Ollama."""
import base64
import json
import os

import httpx


def _request(model: str, messages: list[dict], vision: bool = False) -> str:
    base = os.getenv("MODEL_BASE_URL", "").rstrip("/")
    if not base or not model:
        raise RuntimeError("未配置 MODEL_BASE_URL 和相应模型；请配置本地 Ollama 或云端兼容接口")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    headers = {"Authorization": "Bearer " + os.getenv("MODEL_API_KEY", "ollama")}
    with httpx.Client(timeout=120) as client:
        response = client.post(url, headers=headers, json={"model": model, "messages": messages, "temperature": 0.2})
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


def structured(prompt: str) -> dict:
    raw = _request(os.getenv("TEXT_MODEL", ""), [
        {"role": "system", "content": "你是严谨的教材编辑。只返回 JSON，不要 Markdown。所有依据必须逐字来自输入原文。"},
        {"role": "user", "content": prompt},
    ])
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def ocr_png(png: bytes) -> str:
    encoded = base64.b64encode(png).decode()
    return _request(os.getenv("OCR_MODEL", ""), [
        {"role": "user", "content": [
            {"type": "text", "text": "逐字识别这页教材，保留段落、公式和表格内容。只输出识别文本。"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
        ]},
    ])
