"""Оркестратор: один прогон = одна статья.

Запускается из systemd timer. Что делает:
1. Берёт следующую тему из topic_planner.
2. Генерирует статью через Claude.
3. Генерирует обложку через DALL-E.
4. Сохраняет драфт в БД.
5. Шлёт в Telegram владельцу.

Не публикует напрямую. Публикация — отдельный модуль publish.py.
"""
from __future__ import annotations

import logging
import sys
import traceback

import config
import db
import image_gen
import llm_writer
import notify
import topic_planner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_DIR / "main.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("main")


def run_once() -> int:
    db.init_schema()
    topic = topic_planner.next_topic()
    if topic is None:
        log.warning("Все темы закончились — пополни topics_seed.json")
        notify.send_text(
            "⚠️ blog_dzen: темы закончились. Добавь новые в topics_seed.json."
        )
        return 1

    log.info("Тема: %s (%s)", topic["slug"], topic["title_hint"])

    article = llm_writer.write_article(topic["title_hint"], topic["angle"])
    log.info("Статья сгенерирована: %s (тело %d знаков)",
             article["title"], len(article["body"]))

    # Убираем кавычки-ёлочки и прямые в начале/конце title, lead, body.
    def _strip_quotes(s: str) -> str:
        s = s.strip()
        pairs = [('«', '»'), ('"', '"'), ('"', '"'), ("'", "'")]
        changed = True
        while changed and s:
            changed = False
            for a, b in pairs:
                if s.startswith(a) and s.endswith(b) and len(s) >= 2:
                    s = s[len(a):-len(b)].strip()
                    changed = True
        return s

    article["title"] = _strip_quotes(article["title"])
    article["lead"] = _strip_quotes(article["lead"])
    article["body"] = _strip_quotes(article["body"])

    # Гарантируем, что подпись «До новых идей.» — ровно последняя строка.
    body = article["body"].rstrip()
    # Если LLM сама поставила финал — отрезаем его, чтобы поставить чисто в конец.
    import re as _re
    body = _re.sub(r"\s*До новых идей\.?\s*$", "", body).rstrip()
    article["body"] = body + "\n\nДо новых идей."

    cover = image_gen.generate_cover(topic["keywords"], topic["slug"])

    post_id = db.save_draft(topic["slug"], article["title"],
                            article["body"], str(cover))
    db.mark_topic_used(topic["slug"])

    notify.send_draft(article["title"], article["lead"], article["body"],
                      cover, post_id)
    log.info("Драфт #%d отправлен владельцу", post_id)
    return 0


def main() -> int:
    try:
        return run_once()
    except Exception:
        tb = traceback.format_exc()
        log.error("Падение в main:\n%s", tb)
        try:
            notify.send_text(f"⚠️ blog_dzen упал:\n{tb[-1500:]}")
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
