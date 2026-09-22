import os
from pathlib import Path


def app_secret() -> str:
    configured = os.getenv("APP_SECRET", "").strip()
    if configured:
        return configured
    secret_file = os.getenv("APP_SECRET_FILE", "").strip()
    if secret_file:
        try:
            return Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""
