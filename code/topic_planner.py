"""Загрузка стартовых тем в БД и выбор очередной темы для статьи."""
from __future__ import annotations

import json
import logging

import config
import db

log = logging.getLogger(__name__)


def load_seed_into_db() -> None:
    """Идемпотентно подгружает темы из topics_seed.json в БД."""
    db.init_schema()
    if not config.TOPICS_SEED_PATH.exists():
        log.warning("topics_seed.json не найден")
        return
    with open(config.TOPICS_SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    for t in seed:
        db.upsert_topic(t["slug"], t["title_hint"], t["keywords"], t["angle"])
    log.info("Темы подгружены, остаток неиспользованных: %d", db.topics_left())


def next_topic() -> dict | None:
    load_seed_into_db()
    return db.pick_unused_topic()
