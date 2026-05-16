"""Загрузка конфигурации из .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / ".env")


def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"В .env не задана переменная {name}")
    return v


PROXYAPI_KEY = _required("PROXYAPI_KEY")

TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
TELEGRAM_OWNER_CHAT_ID = int(_required("TELEGRAM_OWNER_CHAT_ID"))

LLM_MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "dall-e-3")
IMAGE_SIZE = os.environ.get("IMAGE_SIZE", "1792x1024")

DZEN_STATE_PATH = Path(os.environ.get(
    "DZEN_STATE_PATH", str(_HERE / "dzen_state.json")
))
IMAGES_DIR = Path(os.environ.get("IMAGES_DIR", str(_HERE.parent / "images")))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = _HERE / "blog.db"
LOG_DIR = _HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Базовые URL ProxyAPI — общий ключ работает для обеих платформ.
PROXYAPI_ANTHROPIC_BASE = "https://api.proxyapi.ru/anthropic"
PROXYAPI_OPENAI_BASE = "https://api.proxyapi.ru/openai/v1"

TOPICS_SEED_PATH = _HERE / "topics_seed.json"
