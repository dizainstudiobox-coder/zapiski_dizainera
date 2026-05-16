"""Отправка драфта статьи в Telegram владельцу."""
from __future__ import annotations

import logging
from pathlib import Path

import requests

import config

log = logging.getLogger(__name__)


TG_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def send_text(text: str) -> dict:
    r = requests.post(
        f"{TG_API}/sendMessage",
        json={"chat_id": config.TELEGRAM_OWNER_CHAT_ID, "text": text,
              "disable_web_page_preview": True},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def send_photo_with_caption(photo_path: Path, caption: str) -> dict:
    """Telegram-caption максимум 1024 символа — обрезаем."""
    caption = caption[:1020] + ("…" if len(caption) > 1020 else "")
    with open(photo_path, "rb") as f:
        r = requests.post(
            f"{TG_API}/sendPhoto",
            data={"chat_id": config.TELEGRAM_OWNER_CHAT_ID,
                  "caption": caption},
            files={"photo": f},
            timeout=60,
        )
    r.raise_for_status()
    return r.json()


def send_draft(title: str, lead: str, body: str, cover: Path,
               post_id: int) -> None:
    """Шлёт обложку с подписью, потом тело отдельным сообщением."""
    head = (
        f"📝 Драфт #{post_id}\n\n"
        f"Заголовок: {title}\n\n"
        f"Lead: {lead}\n\n"
        f"Чтобы опубликовать в Дзен — напиши в этот чат:\n"
        f"/publish_dzen {post_id}\n"
        f"Чтобы отклонить:\n"
        f"/reject_dzen {post_id}"
    )
    send_photo_with_caption(cover, head)

    # Тело может быть длинным — режем на куски по 3500 символов
    chunk_size = 3500
    for i in range(0, len(body), chunk_size):
        send_text(body[i:i + chunk_size])
