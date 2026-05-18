"""Постинг статьи в Дзен через Playwright (chromium headless).

Зависит от storage_state.json (DZEN_STATE_PATH) — авторизация заранее
получается на ноуте через Cookie Editor (dzen.ru + yandex.ru) и переносится
на сервер. См. server_blog_dzen.md в памяти.

Архитектура UI Дзена (важно!):
  - В редакторе /edit заполняется ТОЛЬКО тело статьи.
  - После клика «Опубликовать» (article-publish-btn) открывается
    модалка предпросмотра. В ней лежат:
      * <textarea data-testid="article-title-input">  — заголовок
      * <div data-testid="zen-image-cover">           — превью обложки (клик
                                                       открывает file chooser)
      * <button data-testid="publish-btn">            — финальная публикация
        (изначально disabled, активируется, когда заголовок и обложка готовы)

Flow:
  1.  Открыть Студию /profile/editor/id/{CHANNEL_ID}
  2.  Проверить авторизацию
  3.  Закрыть приветственные модалки
  4.  «+» → «Написать статью» → /edit
  5.  Ввести ТЕЛО ([aria-describedby="placeholder-ZenDraftEditor"])
  6.  Кликнуть article-publish-btn  → модалка
  7.  Залить заголовок в article-title-input
  8.  Кликнуть zen-image-cover, перехватить file chooser и подсунуть обложку
  9.  Дождаться, пока publish-btn станет enabled
  10. Кликнуть publish-btn
  11. Дождаться редиректа на /a/ или другой не-/edit URL
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

    log.info("Playwright force-click на add-publication-button")
    add_btn = page.locator('[data-testid="add-publication-button"]').first
    add_btn.wait_for(state="visible", timeout=15000)
    add_btn.click(force=True)
    page.wait_for_timeout(3000)
    _save_snapshot(page, "03-after-add-click")

    log.info("Клик по «Написать статью»")
    write_article = page.locator('[aria-label="Написать статью"]').first
    if write_article.count() == 0:
        write_article = page.get_by_text("Написать статью", exact=True).first
    write_article.wait_for(state="visible", timeout=10000)
    write_article.click(force=True)

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

    page.wait_for_timeout(2000)
    _save_snapshot(page, "06-editor-opened")


def _fill_body(page: Page, body_md: str) -> None:
    """Заполняет тело статьи Draft.js (ZenDraftEditor) через keyboard.type."""
    body_field = page.locator(
        '[aria-describedby="placeholder-ZenDraftEditor"]'
    ).first
    body_field.wait_for(state="visible", timeout=10000)
    body_field.click(force=True)
    page.wait_for_timeout(400)

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


def _open_preview_modal(page: Page) -> None:
    """Клик по article-publish-btn — открывает модалку предпросмотра."""
    btn = page.locator('[data-testid="article-publish-btn"]').first
    btn.wait_for(state="visible", timeout=10000)
    btn.click(force=True)
    log.info("Клик по article-publish-btn (открываем модалку предпросмотра)")
    # Дождёмся появления заголовочного textarea в модалке
    page.locator('[data-testid="article-title-input"]').first.wait_for(
        state="visible", timeout=15000
    )
    page.wait_for_timeout(800)
    _save_snapshot(page, "10-preview-modal-opened")


def _fill_title_in_modal(page: Page, title: str) -> None:
    """В модалке заполняет textarea заголовка."""
    title_input = page.locator('[data-testid="article-title-input"]').first
    title_input.wait_for(state="visible", timeout=10000)
    title_input.click()
    title_input.fill("")  # очистка на всякий
    title_input.fill(title)
    log.info("Заголовок в модалке введён: %s", title[:80])
    page.wait_for_timeout(500)
    _save_snapshot(page, "11-modal-title-filled")


def _list_file_inputs(page: Page, tag: str) -> list[dict]:
    """Diagnostic: перечисляет все input[type=file] на странице через JS."""
    info = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('input[type="file"]')).map(i => ({
            accept: i.accept || '',
            name: i.name || '',
            id: i.id || '',
            hiddenAttr: i.hidden,
            displayNone: getComputedStyle(i).display === 'none',
            offsetParentNull: i.offsetParent === null,
            outer: i.outerHTML.substring(0, 240)
        }))
        """
    )
    log.info("[%s] input[type=file] в DOM: %d", tag, len(info))
    for idx, item in enumerate(info):
        log.info(
            "  [%s] input#%d: accept=%r name=%r id=%r hidden=%s display:none=%s",
            tag, idx, item["accept"], item["name"], item["id"],
            item["hiddenAttr"], item["displayNone"],
        )
        log.info("    outer: %s", item["outer"])
    return info


def _try_set_image_input(page: Page, cover_path: Path) -> bool:
    """Ищет input[type=file] с accept=image/* (или просто первый) и грузит файл.

    Использует Playwright set_input_files напрямую — работает даже на
    display:none инпутах, не требует открытия системного file chooser.
    """
    inputs = _list_file_inputs(page, "before_set")
    if not inputs:
        return False

    target_idx = 0
    for i, info in enumerate(inputs):
        if info["accept"] and "image" in info["accept"].lower():
            target_idx = i
            break

    try:
        page.locator('input[type="file"]').nth(target_idx).set_input_files(
            str(cover_path)
        )
        log.info("set_input_files выполнен на input#%d", target_idx)
        return True
    except Exception as exc:
        log.warning("set_input_files упал на input#%d: %s", target_idx, exc)
        return False


def _upload_cover_in_modal(page: Page, cover_path: Path) -> None:
    """Загружает обложку в модалке предпросмотра.

    Несколько стратегий по убыванию надёжности:
      1. Клик по zen-image-cover + expect_file_chooser (стандартный путь)
      2. Клик по zen-image-cover → ждём, что Дзен инжектирует скрытый input в DOM
         → set_input_files напрямую на этот input
      3. Если input уже есть в DOM до клика — set_input_files без клика
    """
    cover_target = page.locator('[data-testid="zen-image-cover"]').first
    cover_target.wait_for(state="visible", timeout=10000)

    # 0) Diagnostic: список input до клика
    _list_file_inputs(page, "pre-click")

    # 1) Попытка через file chooser
    try:
        with page.expect_file_chooser(timeout=5000) as fc_info:
            cover_target.click(force=True)
        fc = fc_info.value
        fc.set_files(str(cover_path))
        log.info("Обложка ушла в file chooser zen-image-cover")
        page.wait_for_timeout(5000)
        _save_snapshot(page, "12-cover-via-chooser")
        return
    except PWTimeout:
        log.warning("expect_file_chooser таймаут — пробую set_input_files")

    # 2) Клик мог инжектировать input в DOM — ждём и ищем
    page.wait_for_timeout(1500)
    if _try_set_image_input(page, cover_path):
        log.info("Обложка ушла через set_input_files (после клика)")
        page.wait_for_timeout(5000)
        _save_snapshot(page, "12-cover-via-set-input")
        return

    # 3) Возможно клик не сработал — пробуем кликнуть ещё раз без force,
    #    плюс на дочерний placeholder
    try:
        placeholder = page.locator(
            '[data-testid="zen-image-cover"] [class*="placeholder"]'
        ).first
        if placeholder.count():
            placeholder.click()
            page.wait_for_timeout(1500)
            if _try_set_image_input(page, cover_path):
                log.info("Обложка ушла через set_input_files (placeholder-click)")
                page.wait_for_timeout(5000)
                _save_snapshot(page, "12-cover-via-placeholder")
                return
    except Exception as exc:
        log.debug("placeholder-click не сработал: %s", exc)

    log.warning("Не удалось загрузить обложку никаким способом")
    _save_snapshot(page, "12-cover-failed")


def _click_final_publish(page: Page) -> None:
    """Ждёт активации publish-btn и кликает."""
    publish = page.locator('[data-testid="publish-btn"]').first
    publish.wait_for(state="visible", timeout=10000)

    # Кнопка может быть disabled пока обложка догружается. Подождём.
    deadline = time.time() + 30
    while time.time() < deadline:
        disabled = publish.get_attribute("disabled")
        if disabled is None:
            break
        page.wait_for_timeout(1000)
    else:
        _save_snapshot(page, "13-publish-still-disabled")
        log.warning("publish-btn так и осталась disabled — пробую кликнуть с force=True")

    publish.click(force=True)
    log.info("Клик по financial publish-btn")
    page.wait_for_timeout(3000)
    _save_snapshot(page, "14-after-final-publish")


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
            _fill_body(page, body_md)

            _open_preview_modal(page)
            _fill_title_in_modal(page, title)
            _upload_cover_in_modal(page, cover_path)

            _click_final_publish(page)

            # Ждём редиректа на страницу публикации
            deadline = time.time() + 60
            published_url = None
            while time.time() < deadline:
                url = page.url
                if "/a/" in url or "/media/" in url or "/profile/publications" in url:
                    published_url = url
                    break
                # Также модалка может закрыться, оставив /edit — это тоже признак успеха
                # (Дзен иногда не редиректит, а показывает «Опубликовано»)
                page.wait_for_timeout(1500)

            page.wait_for_timeout(2000)
            final_url = page.url
            ctx.storage_state(path=str(config.DZEN_STATE_PATH))
            _save_snapshot(page, "99-published")
            log.info("Финальный URL: %s", final_url)

            if not published_url and final_url.rstrip("/").endswith("/edit"):
                # Дополнительная проверка: модалка ещё открыта или закрылась?
                try:
                    modal_still_open = (
                        page.locator('[data-testid="publish-btn"]').count() > 0
                    )
                except Exception:
                    modal_still_open = False
                if modal_still_open:
                    raise RuntimeError(
                        "Публикация не завершилась — URL остался /edit и модалка "
                        "предпросмотра всё ещё открыта. См. 14-after-final-publish.html "
                        "и 99-published.html."
                    )
                log.warning(
                    "URL остался /edit, но модалка закрылась — считаем публикацию "
                    "успешной (Дзен не всегда редиректит сразу)."
                )

            return published_url or final_url

        except Exception as exc:
            _save_snapshot(page, "ZZ-final-failure")
            log.error("Поток упал: %s\n%s", exc, traceback.format_exc())
            raise RuntimeError(f"Playwright: {exc}") from exc
        finally:
            browser.close()
