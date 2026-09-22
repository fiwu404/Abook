import re

import httpx

from .db import now
from .models import ProviderCatalog


CATALOG_ID = 1
CATALOG_URL = "https://update.fiwu.cc/modlist"
PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def initialize_provider_catalog(db) -> ProviderCatalog:
    catalog = db.get(ProviderCatalog, CATALOG_ID)
    if not catalog:
        catalog = ProviderCatalog(id=CATALOG_ID, source_url=CATALOG_URL, entries=[], error="")
        db.add(catalog)
        db.commit()
    return catalog


def parse_provider_catalog(payload) -> list[dict]:
    if not isinstance(payload, list):
        raise ValueError("供应商目录必须是 JSON 数组")
    parsed = []
    seen = set()
    for raw in payload[:500]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        if not name or name in seen:
            continue
        urls = {}
        for key in ("url_openai", "url_anthropic"):
            value = str(raw.get(key, "none")).strip()
            if value not in {"input", "none"} and not re.match(r"^https?://", value, re.I):
                value = "none"
            urls[key] = value
        if urls["url_openai"] == urls["url_anthropic"] == "none":
            continue
        seen.add(name)
        parsed.append({"name": name, **urls,
                       "preferred_style": "openai" if urls["url_openai"] != "none" else "anthropic",
                       "variables": sorted(set(PLACEHOLDER.findall(
                           urls["url_openai"] + " " + urls["url_anthropic"])))})
    if not parsed:
        raise ValueError("供应商目录没有可用条目")
    return parsed


def refresh_provider_catalog(db, client_factory=None) -> ProviderCatalog:
    catalog = initialize_provider_catalog(db)
    factory = client_factory or (lambda: httpx.Client(timeout=20, follow_redirects=True))
    try:
        with factory() as client:
            response = client.get(catalog.source_url)
            response.raise_for_status()
            entries = parse_provider_catalog(response.json())
        catalog.entries = entries
        catalog.fetched_at = now()
        catalog.error = ""
        db.commit()
    except Exception as exc:
        db.rollback()
        catalog = initialize_provider_catalog(db)
        catalog.error = str(exc)[:500]
        db.commit()
        raise
    return catalog


def provider_catalog_view(catalog: ProviderCatalog) -> dict:
    return {"source_url": catalog.source_url, "entries": catalog.entries or [],
            "fetched_at": catalog.fetched_at, "error": catalog.error}
