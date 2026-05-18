"""Постинг статьи в Дзен через Playwright (chromium headless).

Зависит от storage_state.json (DZEN_STATE_PATH) — авторизация заранее
получается на ноуте через auth_dzen.py и переносится на сервер.

После каждой важной точки шага сохраняем PNG + HTML в failures/ —
чтобы можно было разобраться, если упадёт.
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

EDITOR_URL = "https://dzen.ru/profile/editor/addpost?type=article"
PROFILE_URL = "https://dzen.ru/profile/editor"

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


def _ensure_logged_in(ctx: BrowserContext) -> None:
    page = ctx.new_page()
    try:
        page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        cur = page.url
        if "passport" in cur or "login" in cur:
            _save_snapshot(page, "auth-redirect")
            raise RuntimeError("Куки Дзена просрочены — нужно перевыпустить state.json")
        log.info("Авторизация в Дзене ОК (url=%s)", cur)
    finally:
        page.close()


def _click_button(page: Page, *labels: str, timeout: int = 5000) -> bool:
    for text in labels:
        try:
            page.get_by_role("button", name=text).first.click(timeout=timeout)
            log.info("Клик по «%s»", text)
            return True
        except Exception:
            pass
    return False


def publish_article(title: str, body_md: str, cover_path: Path) -> str:
    if not config.DZEN_STATE_PATH.exists():
        raise RuntimeError(
            f"Не найден файл сессии {config.DZEN_STATE_PATH}. "
            "Сгенерируй его через auth_dzen.py."
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
        page = None
        try:
            _ensure_logged_in(ctx)

            page = ctx.new_page()
            log.info("Открываю редактор: %s", EDITOR_URL)
            page.goto(EDITOR_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)

            # === ВАЖНО: сохраняем снимок страницы СРАЗУ ===
            _save_snapshot(page, "01-editor-opened")
            log.info("URL после открытия редактора: %s", page.url)

            # Если редирект — это диагностично
            if "addpost" not in page.url and "editor" not in page.url:
                _save_snapshot(page, "02-unexpected-url")
                raise RuntimeError(f"Редиректнуло куда-то не туда: {page.url}")

            # 1. Заголовок — пробуем расширенный список селекторов
            title_candidates = [
                'textarea[placeholder="Заголовок"]',
                'textarea[aria-label="Заголовок"]',
                'input[placeholder="Заголовок"]',
                'input[aria-label="Заголовок"]',
                'h1[contenteditable="true"]',
                '[data-testid*="title"]',
                '[data-testid*="header"]',
                '[class*="title-input" i]',
                '[class*="TitleInput" i]',
                '[class*="header-input" i]',
                'div[contenteditable="true"][role="textbox"]',
                'div[contenteditable="true"]',
            ]
            title_clicked = False
            for sel in title_candidates:
                try:
                    loc = page.locator(sel).first
                    loc.click(timeout=3000)
                    loc.fill(title) if "input" in sel or "textarea" in sel else loc.type(title)
                    log.info("Заголовок введён через селектор: %s", sel)
                    title_clicked = True
                    break
                except Exception as e:
                    log.debug("заголовок не подошёл селектор %s: %s", sel, e)
            if not title_clicked:
                _save_snapshot(page, "03-no-title-field")
                raise RuntimeError("Не нашёл поле заголовка ни по одному селектору")

            page.wait_for_timeout(500)
            _save_snapshot(page, "04-title-typed")

            # 2. Тело
            body_candidates = [
                '[data-testid*="body"]',
                '[data-testid*="content"]',
                '[class*="content-editor" i]',
                '[class*="ContentEditor" i]',
                'div[contenteditable="true"][role="textbox"]',
                'div.ProseMirror[contenteditable="true"]',
                'div[contenteditable="true"]',
            ]
            body_typed = False
            for sel in body_candidates:
                try:
                    locs = page.locator(sel).all()
                    # Пробуем найти именно редактор тела, не поле заголовка
                    for loc in locs:
                        try:
                            placeholder = loc.get_attribute("aria-label") or ""
                            if "загол" in placeholder.lower():
                                continue
                            loc.click(timeout=2000)
                            loc.type(body_md, delay=10)
                            log.info("Тело введено через %s (%d символов)", sel, len(body_md))
                            body_typed = True
                            break
                        except Exception:
                            continue
                    if body_typed:
                        break
                except Exception:
                    continue
            if not body_typed:
                _save_snapshot(page, "05-no-body-editor")
                raise RuntimeError("Не нашёл редактор тела статьи")

            page.wait_for_timeout(800)
            _save_snapshot(page, "06-body-typed")

            # 3. Обложка
            try:
                file_input = page.locator('input[type="file"]').first
                file_input.set_input_files(str(cover_path))
                log.info("Обложка загружена: %s", cover_path)
                page.wait_for_timeout(4000)
                _save_snapshot(page, "07-cover-uploaded")
            except Exception as exc:
                log.warning("обложку не загрузил: %s", exc)
                _save_snapshot(page, "07-no-cover-input")

            # 4. Опубликовать
            if not _click_button(page, "Опубликовать", "Опубликовать сейчас"):
                _save_snapshot(page, "08-no-publish-btn")
                raise RuntimeError("Не нашёл кнопку Опубликовать")

            page.wait_for_timeout(2000)
            _click_button(page, "Опубликовать", "Подтвердить", "Да, опубликовать", timeout=3000)

            t0 = time.time()
            while time.time() - t0 < 30:
                if "/a/" in page.url or "/profile/editor" not in page.url:
                    break
                page.wait_for_timeout(1000)

            url = page.url
            log.info("Опубликовано: %s", url)
            ctx.storage_state(path=str(config.DZEN_STATE_PATH))
            _save_snapshot(page, "99-published")
            return url

        except Exception as exc:
            if page is not None:
                _save_snapshot(page, "ZZ-final-failure")
            log.error("Поток упал: %s\n%s", exc, traceback.format_exc())
            raise RuntimeError(f"Playwright: {exc}") from exc
        finally:
            browser.close()
