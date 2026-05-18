"""Постинг статьи в Дзен через Playwright (chromium headless).

Зависит от storage_state.json (DZEN_STATE_PATH) — авторизация заранее
получается на ноуте через Cookie Editor (dzen.ru + yandex.ru) и переносится
на сервер. См. server_blog_dzen.md в памяти.

Flow:
  1. Открыть Студию  /profile/editor/id/{CHANNEL_ID}
  2. Проверить авторизацию (по URL и DOM)
  3. Закрыть приветственные модалки (Esc + ручной snip)
  4. Force-кликнуть кнопку добавления публикации
  5. Force-кликнуть «Написать статью»
  6. Дождаться URL .../edit  →  Draft.js редактор
  7. Заполнить заголовок ([role=textbox spellcheck=false]) через keyboard.type
  8. Заполнить тело ([aria-describedby=placeholder-ZenDraftEditor]) тем же способом
  9. Загрузить обложку через input[type=file]
  10. Кликнуть [data-testid=article-publish-btn]
  11. Подтвердить публикацию в модалке (если есть)
  12. Дождаться редиректа на страницу публикации

После каждой важной точки шага сохраняем PNG + HTML в failures/.
"""
from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import (BrowserContext, Page,
                                 TimeoutError as PWTimeout,
                                 sync_playwright)

import config

log = logging.getLogger(__name__)

CHANNEL_ID = "698cd7315895d9016571cee9"
STUDIO_URL = f"https://dzen.ru/profile/editor/id/{CHANNEL_ID}"

FAILURES_DIR = Path(__file__).resolve().parent / "failures"
FAILURES_DIR.mkdir(parents=True, exist_ok=True)


def _save_snapshot(page: Page, tag: str) -> None:
    """Сохраняет PNG + HTML текущей страницы под именем <ts>-<tag>.{png,html}."""
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    base = FAILURES_DIR / f"{ts}-{tag}"
    try:
        page.screenshot(path=f"{base}.png", full_page=True)
        log.info("snapshot PNG: %s.png", base)
    except Exception as exc:
        log.warning("PNG не сохранён (%s): %s", tag, exc)
    try:
        Path(f"{base}.html").write_text(page.content(), encoding="utf-8")
        log.info("snapshot HTML: %s.html", base)
    except Exception as exc:
        log.warning("HTML не сохранён (%s): %s", tag, exc)


def _dismiss_overlays(page: Page) -> None:
    """Закрывает модалки и оверлеи, которые блокируют клики."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        page.evaluate(
            """
            () => {
                const selectors = [
                    '[data-testid="modal-overlay"]',
                    '[class*="editor--modal__overlay"]',
                    '[class*="editor--modal__rootElement"]',
                    '[class*="modal__overlay"]',
                    '[class*="onboarding"]',
                ];
                selectors.forEach(sel => {
                    document.querySelectorAll(sel).forEach(e => e.remove());
                });
            }
            """
        )
    except Exception:
        pass


def _ensure_logged_in(page: Page) -> None:
    """Открывает Студию и убеждается, что мы залогинены как автор канала."""
    log.info("Открываю Студию: %s", STUDIO_URL)
    page.goto(STUDIO_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)

    cur = page.url
    if "passport" in cur or "login" in cur:
        _save_snapshot(page, "auth-redirect")
        raise RuntimeError(f"Куки Дзена просрочены — редирект на {cur}")

    # Если редиректнуло на публичный профиль без /editor/ — авторизация не прошла как автор
    if "/profile/editor/id/" not in cur:
        _save_snapshot(page, "auth-not-author")
        raise RuntimeError(
            f"Не в Студии после захода (url={cur}). "
            "Скорее всего сессия yandex.ru невалидна."
        )
    log.info("Авторизация в Дзене ОК (url=%s)", cur)


def _open_editor(page: Page) -> None:
    """Из Студии открывает редактор статьи и ждёт URL .../edit."""
    _dismiss_overlays(page)
    page.wait_for_timeout(500)
    _save_snapshot(page, "02-after-modal-close")

    # Кнопка «+» с data-testid="add-publication-button"
    log.info("Playwright force-click на add-publication-button")
    add_btn = page.locator('[data-testid="add-publication-button"]').first
    add_btn.wait_for(state="visible", timeout=15000)
    add_btn.click(force=True)
    page.wait_for_timeout(3000)
    _save_snapshot(page, "03-after-add-click")

    # В выпадающем меню — «Написать статью»
    log.info("Клик по «Написать статью»")
    write_article = page.locator('[aria-label="Написать статью"]').first
    if write_article.count() == 0:
        write_article = page.get_by_text("Написать статью", exact=True).first
    write_article.wait_for(state="visible", timeout=10000)
    write_article.click(force=True)

    # Ждём, пока URL станет .../edit
    try:
        page.wait_for_url(
            lambda url: url.rstrip("/").endswith("/edit") or "/edit?" in url,
            timeout=20000,
        )
    except Exception:
        pass

    page.wait_for_timeout(2500)
    _save_snapshot(page, "05-after-article-select")
    log.info("URL после выбора «Написать статью»: %s", page.url)

    if not (page.url.rstrip("/").endswith("/edit") or "/edit?" in page.url):
        raise RuntimeError(
            f"Ожидался переход в редактор статьи (URL .../edit), "
            f"но URL={page.url}. См. 05-after-article-select.html."
        )

    # Дать Draft.js дорендериться
    page.wait_for_timeout(2000)
    _save_snapshot(page, "06-editor-opened")


def _fill_title(page: Page, title: str) -> None:
    """Заполняет заголовок Draft.js через keyboard.type."""
    # role=textbox + spellcheck=false — единственное такое поле (заголовок)
    title_field = page.locator('[role="textbox"][spellcheck="false"]').first
    title_field.wait_for(state="visible", timeout=10000)
    title_field.click(force=True)
    page.wait_for_timeout(300)
    page.keyboard.type(title, delay=15)
    log.info("Заголовок введён: %s", title[:60])
    page.wait_for_timeout(500)
    _save_snapshot(page, "07-title-typed")


def _fill_body(page: Page, body_md: str) -> None:
    """Заполняет тело статьи Draft.js (ZenDraftEditor)."""
    body_field = page.locator(
        '[aria-describedby="placeholder-ZenDraftEditor"]'
    ).first
    body_field.wait_for(state="visible", timeout=10000)
    body_field.click(force=True)
    page.wait_for_timeout(300)

    # Markdown-разметка Дзеном не парсится, но абзацы Enter работает.
    paragraphs = [p.strip() for p in body_md.split("\n\n") if p.strip()]
    for idx, para in enumerate(paragraphs):
        for line_idx, line in enumerate(para.split("\n")):
            if line_idx > 0:
                page.keyboard.press("Shift+Enter")
            page.keyboard.type(line, delay=5)
        if idx < len(paragraphs) - 1:
            page.keyboard.press("Enter")
            page.wait_for_timeout(80)

    log.info("Тело введено (%d символов, %d абзацев)", len(body_md), len(paragraphs))
    page.wait_for_timeout(1000)
    _save_snapshot(page, "08-body-typed")


def _upload_cover(page: Page, cover_path: Path) -> None:
    """Грузит обложку через первый input[type=file]."""
    try:
        file_input = page.locator('input[type="file"]').first
        if file_input.count() == 0:
            log.warning("input[type=file] не найден — обложку не загружаю")
            return
        file_input.set_input_files(str(cover_path))
        log.info("Обложка загружена: %s", cover_path)
        page.wait_for_timeout(4000)
        _save_snapshot(page, "09-cover-uploaded")
    except Exception as exc:
        log.warning("Не получилось загрузить обложку: %s", exc)
        _save_snapshot(page, "09-cover-failed")


def _click_publish(page: Page) -> None:
    """Клик по кнопке Опубликовать + подтверждение в модалке."""
    btn = page.locator('[data-testid="article-publish-btn"]').first
    btn.wait_for(state="visible", timeout=10000)
    btn.click(force=True)
    log.info("Клик по кнопке «Опубликовать»")
    page.wait_for_timeout(2500)
    _save_snapshot(page, "10-after-publish-click")

    # В Дзене может появиться модалка подтверждения
    for label in (
        "Опубликовать сейчас",
        "Опубликовать",
        "Подтвердить",
        "Да, опубликовать",
    ):
        try:
            page.get_by_role("button", name=label).first.click(timeout=2500)
            log.info("Подтвердил публикацию: «%s»", label)
            page.wait_for_timeout(1500)
            break
        except Exception:
            continue


def publish_article(title: str, body_md: str, cover_path: Path) -> str:
    if not config.DZEN_STATE_PATH.exists():
        raise RuntimeError(
            f"Не найден файл сессии {config.DZEN_STATE_PATH}. "
            "Экспортируй куки dzen.ru + yandex.ru через Cookie Editor "
            "и собери storage_state."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            storage_state=str(config.DZEN_STATE_PATH),
            locale="ru-RU",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        try:
            _ensure_logged_in(page)
            _save_snapshot(page, "01-studio-opened")

            _open_editor(page)

            _fill_title(page, title)
            _fill_body(page, body_md)

            _upload_cover(page, cover_path)

            _click_publish(page)

            # Ждём редиректа на страницу публикации
            deadline = time.time() + 45
            published_url = None
            while time.time() < deadline:
                url = page.url
                if "/a/" in url or "/media/" in url:
                    published_url = url
                    break
                page.wait_for_timeout(1000)

            page.wait_for_timeout(2000)
            url = page.url
            log.info("Финальный URL: %s", url)
            ctx.storage_state(path=str(config.DZEN_STATE_PATH))
            _save_snapshot(page, "99-published")
            return published_url or url

        except Exception as exc:
            _save_snapshot(page, "ZZ-final-failure")
            log.error("Поток упал: %s\n%s", exc, traceback.format_exc())
            raise RuntimeError(f"Playwright: {exc}") from exc
        finally:
            browser.close()
