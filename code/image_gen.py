"""Генерация обложки через DALL-E 3 (ProxyAPI). Сохраняет PNG локально."""
from __future__ import annotations

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
        "response_format": "url",
    }
    headers = {
        "Authorization": f"Bearer {config.PROXYAPI_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{config.PROXYAPI_OPENAI_BASE}/images/generations"

    log.info("Image-gen запрос: %s", config.IMAGE_MODEL)
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()

    image_url = data["data"][0]["url"]
    img_resp = requests.get(image_url, timeout=60)
    img_resp.raise_for_status()

    out_path = config.IMAGES_DIR / f"{slug}-{uuid.uuid4().hex[:6]}.png"
    out_path.write_bytes(img_resp.content)
    log.info("Сохранена обложка: %s (%d байт)", out_path, len(img_resp.content))
    return out_path
