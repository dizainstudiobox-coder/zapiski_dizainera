"""Постинг статьи в Дзен через Playwright (chromium headless).

Зависит от storage_state.json (DZEN_STATE_PATH) — авторизация заранее
получается на ноуте через auth_dzen.py и переносится на сервер.

Дзен периодически правит UI, поэтому селекторы тут максимально
устойчивые: ищем по тексту кнопок и aria-label, а не по классам.
Если что-то сломается — смотрим failures/<date>.png и debug HTML.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import (BrowserContext, Page, TimeoutError as PWTimeout,
                                 sync_playwright)

import config

log = logging.getLogger(__name__)

EDITOR_URL = "https://dzen.ru/profile/editor/addpost?type=article"
PROFILE_URL = "https://dzen.ru/profile/editor"

FAILURES_DIR = Path(__file__).resolve().parent / "failures"
FAILURES_DIR.mkdir(exist_ok=True)


def _save_failure(page: Page, tag: str) -> None:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    base = FAILURES_DIR / f"{ts}-{tag}"
    try:
        page.screenshot(path=f"{base}.png", full_page=True)
        (Path(f"{base}.html")).write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        log.warning("не смог сохранить failure: %s", exc)


def _ensure_logged_in(ctx: BrowserContext) -> None:
    """Проверяем что куки живые — открываем editor и смотрим что не редирект на login."""
    page = ctx.new_page()
    try:
        page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        cur = page.url
        if "passport" in cur or "login" in cur:
            _save_failure(page, "auth-redirect")
            raise RuntimeError("Куки Дзена просрочены — нужно перевыпустить state.json")
        log.info("Авторизация в Дзене ОК (url=%s)", cur)
    finally:
        page.close()


def _click_button(page: Page, *labels: str, timeout: int = 5000) -> bool:
    """Пробует кликнуть первую найденную кнопку по русскому тексту."""
    for text in labels:
        try:
            page.get_by_role("button", name=text).first.click(timeout=timeout)
            log.info("Клик по «%s»", text)
            return True
        except Exception:
            pass
    return False


def publish_article(title: str, body_md: str, cover_path: Path) -> str:
    """Возвращает URL опубликованной статьи (если получится определить)."""
    if not config.DZEN_STATE_PATH.exists():
        raise RuntimeError(
            f"Не найден файл сессии {config.DZEN_STATE_PATH}. "
            "Сгенерируй его через auth_dzen.py на локальной машине."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            storage_state=str(config.DZEN_STATE_PATH),
            locale="ru-RU",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/127.0.0.0 Safari/537.36"),
        )
        try:
            _ensure_logged_in(ctx)

            page = ctx.new_page()
            page.goto(EDITOR_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)

            # 1. Заголовок
            title_field = page.locator(
                'textarea[placeholder="Заголовок"], textarea[aria-label="Заголовок"], h1[contenteditable="true"]'
            ).first
            title_field.click(timeout=10000)
            title_field.fill(title)
            log.info("Заголовок введён")
            page.wait_for_timeout(500)

            # 2. Тело — кликаем в редактор и набираем
            editor = page.locator('div[contenteditable="true"]').last
            editor.click()
            editor.type(body_md, delay=15)
            log.info("Тело статьи введено (%d символов)", len(body_md))
            page.wait_for_timeout(500)

            # 3. Обложка — ищем input[type=file]
            try:
                file_input = page.locator('input[type="file"]').first
                file_input.set_input_files(str(cover_path))
                log.info("Обложка загружена: %s", cover_path)
                page.wait_for_timeout(4000)
            except Exception as exc:
                log.warning("обложку не загрузил: %s", exc)
                _save_failure(page, "no-cover-input")

            # 4. Опубликовать
            if not _click_button(page, "Опубликовать", "Опубликовать сейчас"):
                _save_failure(page, "no-publish-btn")
                raise RuntimeError("Не нашёл кнопку Опубликовать")

            # Может быть подтверждение
            page.wait_for_timeout(2000)
            _click_button(page, "Опубликовать", "Подтвердить", "Да, опубликовать", timeout=3000)

            # Ждём навигации на страницу статьи
            t0 = time.time()
            while time.time() - t0 < 30:
                if "/a/" in page.url or "/profile/editor" not in page.url:
                    break
                page.wait_for_timeout(1000)

            url = page.url
            log.info("Опубликовано: %s", url)
            ctx.storage_state(path=str(config.DZEN_STATE_PATH))  # обновляем сессию
            return url
        except PWTimeout as exc:
            _save_failure(page, "timeout")
            raise RuntimeError(f"Playwright timeout: {exc}") from exc
        finally:
            browser.close()
