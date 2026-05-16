"""Опубликовать одобренный драфт по ID или последний approved.

Запускается так:
    .venv/bin/python publish.py [<post_id>]
Если post_id не указан — берёт последний approved.

Этот скрипт может вызывать наш telegram-бот по команде /publish_dzen.
"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

import config
import db
import dzen_poster
import notify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_DIR / "publish.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("publish")


def main() -> int:
    db.init_schema()
    post_id_arg = int(sys.argv[1]) if len(sys.argv) > 1 else None

    if post_id_arg:
        post = db.get_post(post_id_arg)
        if not post:
            print(f"Поста #{post_id_arg} нет")
            return 1
        if post["status"] not in ("approved", "draft"):
            print(f"Пост #{post_id_arg} в статусе {post['status']}, не публикуется")
            return 1
    else:
        post = db.latest_pending() or db.latest_draft()
        if not post:
            print("Нет approved/draft постов")
            return 1

    log.info("Публикуем пост #%d: %s", post["id"], post["title"])
    try:
        url = dzen_poster.publish_article(
            title=post["title"],
            body_md=post["body"],
            cover_path=Path(post["cover_path"]) if post["cover_path"] else None,
        )
    except Exception:
        tb = traceback.format_exc()
        log.error("publish упал:\n%s", tb)
        try:
            notify.send_text(f"⚠️ blog_dzen: публикация упала.\n{tb[-800:]}")
        except Exception:
            pass
        return 2

    db.set_status(post["id"], "published", dzen_url=url)
    notify.send_text(f"✅ Опубликовано в Дзен: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
