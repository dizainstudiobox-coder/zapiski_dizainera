"""Генерация обложки через gpt-image-1 / DALL-E (ProxyAPI). Сохраняет PNG локально."""
from __future__ import annotations

import base64
import logging
import textwrap
import uuid
from pathlib import Path

import requests

import config

log = logging.getLogger(__name__)


PROMPT_TEMPLATE = textwrap.dedent("""\
    Photorealistic interior design photo, magazine quality.
    Subject and mood: {keywords}.
    Style: warm natural light, soft shadows, calm and personal atmosphere,
    no people, no text, no logos. Composition wide, cinematic.
""")


def generate_cover(keywords: str, slug: str) -> Path:
    """Генерирует картинку, сохраняет в IMAGES_DIR/<slug>-<uuid>.png."""
    payload = {
        "model": config.IMAGE_MODEL,
        "prompt": PROMPT_TEMPLATE.format(keywords=keywords),
        "n": 1,
        "size": config.IMAGE_SIZE,
    }
    # response_format добавляем только для моделей DALL-E. gpt-image-1
    # этот параметр не поддерживает и всегда возвращает b64_json.
    if config.IMAGE_MODEL.startswith("dall-e"):
        payload["response_format"] = "url"

    headers = {
        "Authorization": f"Bearer {config.PROXYAPI_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{config.PROXYAPI_OPENAI_BASE}/images/generations"

    log.info("Image-gen запрос: %s, size=%s", config.IMAGE_MODEL, config.IMAGE_SIZE)
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    if r.status_code >= 400:
        log.error("Image-gen ошибка %s: %s", r.status_code, r.text[:500])
        r.raise_for_status()
    data = r.json()

    item = data["data"][0]
    if "b64_json" in item:
        img_bytes = base64.b64decode(item["b64_json"])
    elif "url" in item:
        img_resp = requests.get(item["url"], timeout=60)
        img_resp.raise_for_status()
        img_bytes = img_resp.content
    else:
        raise RuntimeError(f"Неизвестный формат ответа image-gen: keys={list(item)}")

    out_path = config.IMAGES_DIR / f"{slug}-{uuid.uuid4().hex[:6]}.png"
    out_path.write_bytes(img_bytes)
    log.info("Сохранена обложка: %s (%d байт)", out_path, len(img_bytes))
    return out_path
