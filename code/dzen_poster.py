"""Постинг статьи в Дзен через Playwright (chromium headless).

Архитектура UI Дзена:
  - В редакторе /edit заполняется ТОЛЬКО тело.
  - После клика «Опубликовать» (article-publish-btn) открывается модалка предпросмотра:
      * textarea[data-testid="article-title-input"]  — заголовок
      * div[data-testid="zen-image-cover"]           — превью обложки (drop)
      * button[data-testid="publish-btn"]            — финальная публикация
"""
from __future__ import annotations

import base64
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
                const sels = [
                    '[data-testid="modal-overlay"]',
                    '[class*="editor--modal__overlay"]',
                    '[class*="editor--modal__rootElement"]',
                    '[class*="modal__overlay"]',
                    '[class*="onboarding"]',
                ];
                sels.forEach(s => document.querySelectorAll(s).forEach(e => e.remove()));
            }
            """
        )
    except Exception:
        pass


def _ensure_logged_in(page: Page) -> None:
    log.info("Открываю Студию: %s", STUDIO_URL)
    page.goto(STUDIO_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)
    cur = page.url
    if "passport" in cur or "login" in cur:
        _save_snapshot(page, "auth-redirect")
        raise RuntimeError(f"Куки Дзена просрочены — редирект на {cur}")
    if "/profile/editor/id/" not in cur:
        _save_snapshot(page, "auth-not-author")
        raise RuntimeError(f"Не в Студии после захода (url={cur}).")
    log.info("Авторизация в Дзене ОК (url=%s)", cur)


def _open_editor(page: Page) -> None:
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
        raise RuntimeError(f"Не перешли в /edit, url={page.url}")
    page.wait_for_timeout(2000)
    _save_snapshot(page, "06-editor-opened")


def _fill_body(page: Page, body_md: str) -> None:
    body_field = page.locator('[aria-describedby="placeholder-ZenDraftEditor"]').first
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
    btn = page.locator('[data-testid="article-publish-btn"]').first
    btn.wait_for(state="visible", timeout=10000)
    btn.click(force=True)
    log.info("Клик по article-publish-btn (открываем модалку предпросмотра)")
    page.locator('[data-testid="article-title-input"]').first.wait_for(
        state="visible", timeout=15000
    )
    page.wait_for_timeout(800)
    _save_snapshot(page, "10-preview-modal-opened")


def _fill_title_in_modal(page: Page, title: str) -> None:
    title_input = page.locator('[data-testid="article-title-input"]').first
    title_input.wait_for(state="visible", timeout=10000)
    title_input.click()
    title_input.fill("")
    title_input.fill(title)
    log.info("Заголовок в модалке введён: %s", title[:80])
    page.wait_for_timeout(500)
    _save_snapshot(page, "11-modal-title-filled")


def _list_file_inputs(page: Page, tag: str) -> list[dict]:
    info = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('input[type="file"]')).map(i => ({
            accept: i.accept || '',
            name: i.name || '',
            id: i.id || '',
            hiddenAttr: i.hidden,
            displayNone: getComputedStyle(i).display === 'none',
            outer: i.outerHTML.substring(0, 240)
        }))
        """
    )
    log.info("[%s] input[type=file] в DOM: %d", tag, len(info))
    for idx, item in enumerate(info):
        log.info("  [%s] input#%d: accept=%r name=%r id=%r hidden=%s display:none=%s",
                 tag, idx, item["accept"], item["name"], item["id"],
                 item["hiddenAttr"], item["displayNone"])
        log.info("    outer: %s", item["outer"])
    return info


def _try_set_image_input(page: Page, cover_path: Path) -> bool:
    inputs = _list_file_inputs(page, "before_set")
    if not inputs:
        return False
    target_idx = 0
    for i, info in enumerate(inputs):
        if info["accept"] and "image" in info["accept"].lower():
            target_idx = i
            break
    try:
        page.locator('input[type="file"]').nth(target_idx).set_input_files(str(cover_path))
        log.info("set_input_files выполнен на input#%d", target_idx)
        return True
    except Exception as exc:
        log.warning("set_input_files упал: %s", exc)
        return False


def _drop_file_via_js(page: Page, target_selector: str, cover_path: Path) -> dict:
    """Симулирует drag-and-drop файла через JS DataTransfer."""
    data_b64 = base64.b64encode(Path(cover_path).read_bytes()).decode()
    filename = Path(cover_path).name
    ext = filename.lower().rsplit(".", 1)[-1]
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/png")

    result = page.evaluate(
        """([b64, filename, mime, selector]) => {
            const target = document.querySelector(selector);
            if (!target) return { ok: false, reason: 'target not found' };
            const binary = atob(b64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            const blob = new Blob([bytes], { type: mime });
            const file = new File([blob], filename, { type: mime });
            const dt = new DataTransfer();
            dt.items.add(file);
            const rect = target.getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
            for (const evType of ['dragenter','dragover','drop']) {
                const ev = new DragEvent(evType, {
                    bubbles: true, cancelable: true, dataTransfer: dt,
                    clientX: x, clientY: y,
                });
                Object.defineProperty(ev, 'dataTransfer', { value: dt });
                target.dispatchEvent(ev);
            }
            return { ok: true, fileSize: file.size, filename: filename };
        }""",
        [data_b64, filename, mime, target_selector],
    )
    return result or {"ok": False, "reason": "no result"}


def _check_cover_uploaded(page: Page) -> dict:
    """Смотрит на zen-image-cover img.src: blob = локальный фейк, https = CDN."""
    return page.evaluate(
        """
        () => {
            const cover = document.querySelector('[data-testid="zen-image-cover"]');
            if (!cover) return { found: false, reason: 'no zen-image-cover' };
            const img = cover.querySelector('img');
            if (!img) return { found: false, reason: 'no img',
                has_placeholder: !!cover.querySelector('[class*="placeholder"]') };
            const src = img.src || '';
            return {
                found: true,
                src: src.substring(0, 150),
                isBlob: src.startsWith('blob:'),
                isHttps: src.startsWith('http'),
                isEmpty: !src,
            };
        }
        """
    )


def _insert_cover_in_editor(page: Page, cover_path: Path) -> bool:
    """Вставка картинки в начало body через side-button «Вставить изображение»."""
    body_field = page.locator('[aria-describedby="placeholder-ZenDraftEditor"]').first
    if body_field.count() == 0:
        body_field = page.locator('[data-editor="ZenDraftEditor"]').first
    body_field.click(force=True)
    page.wait_for_timeout(300)
    page.keyboard.press("Control+Home")
    page.wait_for_timeout(500)
    _save_snapshot(page, "13a-before-insert-image")

    btn = page.locator('button[data-tip="Вставить изображение"]').first
    if btn.count() == 0:
        log.warning("Кнопка «Вставить изображение» не найдена в редакторе")
        return False

    try:
        with page.expect_file_chooser(timeout=5000) as fc_info:
            btn.click(force=True)
        fc = fc_info.value
        fc.set_files(str(cover_path))
        log.info("Картинка ушла через file chooser side-button")
        page.wait_for_timeout(8000)
        _save_snapshot(page, "13b-image-inserted-chooser")
        return True
    except PWTimeout:
        log.warning("file chooser side-button — таймаут, ищу input[type=file]")

    page.wait_for_timeout(1000)
    if _try_set_image_input(page, cover_path):
        log.info("Картинка ушла через set_input_files (после клика side-button)")
        page.wait_for_timeout(8000)
        _save_snapshot(page, "13b-image-inserted-input")
        return True

    log.warning("Не удалось вставить картинку через side-button")
    return False


def _upload_cover_in_modal(page: Page, cover_path: Path) -> None:
    cover_target = page.locator('[data-testid="zen-image-cover"]').first
    cover_target.wait_for(state="visible", timeout=10000)
    cover_target.scroll_into_view_if_needed()
    page.wait_for_timeout(300)

    log.info("Пробую drag-and-drop через DataTransfer")
    drop_result = _drop_file_via_js(page, '[data-testid="zen-image-cover"]', cover_path)
    log.info("drag-drop результат: %s", drop_result)
    if drop_result.get("ok"):
        page.wait_for_timeout(12000)
        cover_state = _check_cover_uploaded(page)
        log.info("Состояние обложки после drag-drop: %s", cover_state)
        _save_snapshot(page, "12-cover-via-drag")
        if cover_state.get("isHttps"):
            log.info("Обложка реально загружена на CDN Дзена")
            return
        if cover_state.get("isBlob"):
            log.warning("img.src=blob: — Дзен показал preview, но НЕ загрузил")

    log.info("Пробую expect_file_chooser на zen-image-cover")
    _list_file_inputs(page, "pre-click")
    try:
        with page.expect_file_chooser(timeout=5000) as fc_info:
            cover_target.click(force=True)
        fc = fc_info.value
        fc.set_files(str(cover_path))
        log.info("Обложка ушла через file chooser")
        page.wait_for_timeout(8000)
        cover_state = _check_cover_uploaded(page)
        log.info("После file_chooser: %s", cover_state)
        _save_snapshot(page, "12-cover-via-chooser")
        if cover_state.get("isHttps"):
            return
    except PWTimeout:
        log.warning("expect_file_chooser таймаут")

    page.wait_for_timeout(1500)
    if _try_set_image_input(page, cover_path):
        log.info("Обложка ушла через set_input_files")
        page.wait_for_timeout(8000)
        cover_state = _check_cover_uploaded(page)
        log.info("После set_input_files: %s", cover_state)
        _save_snapshot(page, "12-cover-via-set-input")
        if cover_state.get("isHttps"):
            return

    log.warning("Обложка в модалке не загружена (CDN url не получили)")
    _save_snapshot(page, "12-cover-failed")


def _click_final_publish(page: Page) -> None:
    publish = page.locator('[data-testid="publish-btn"]').first
    publish.wait_for(state="visible", timeout=10000)
    deadline = time.time() + 30
    while time.time() < deadline:
        disabled = publish.get_attribute("disabled")
        if disabled is None:
            break
        page.wait_for_timeout(1000)
    else:
        _save_snapshot(page, "13-publish-still-disabled")
        log.warning("publish-btn так и осталась disabled — кликаю force=True")
    publish.click(force=True)
    log.info("Клик по financial publish-btn")
    page.wait_for_timeout(3000)
    _save_snapshot(page, "14-after-final-publish")


def publish_article(title: str, body_md: str, cover_path: Path) -> str:
    if not config.DZEN_STATE_PATH.exists():
        raise RuntimeError(f"Не найден файл сессии {config.DZEN_STATE_PATH}.")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            storage_state=str(config.DZEN_STATE_PATH),
            locale="ru-RU",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/127.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()
        try:
            _ensure_logged_in(page)
            _save_snapshot(page, "01-studio-opened")

            _open_editor(page)
            _fill_body(page, body_md)

            cover_inserted_in_body = _insert_cover_in_editor(page, cover_path)
            log.info("Картинка в начало body: %s", cover_inserted_in_body)

            _open_preview_modal(page)
            _fill_title_in_modal(page, title)
            _upload_cover_in_modal(page, cover_path)

            _click_final_publish(page)

            deadline = time.time() + 60
            published_url = None
            while time.time() < deadline:
                url = page.url
                if "/a/" in url or "/media/" in url or "/profile/publications" in url:
                    published_url = url
                    break
                page.wait_for_timeout(1500)

            page.wait_for_timeout(2000)
            final_url = page.url
            ctx.storage_state(path=str(config.DZEN_STATE_PATH))
            _save_snapshot(page, "99-published")
            log.info("Финальный URL: %s", final_url)

            if not published_url and final_url.rstrip("/").endswith("/edit"):
                try:
                    modal_still_open = (
                        page.locator('[data-testid="publish-btn"]').count() > 0
                    )
                except Exception:
                    modal_still_open = False
                if modal_still_open:
                    raise RuntimeError(
                        "Публикация не завершилась — URL остался /edit и модалка "
                        "предпросмотра всё ещё открыта."
                    )
                log.warning("URL остался /edit, но модалка закрылась — считаем успехом.")
            return published_url or final_url

        except Exception as exc:
            _save_snapshot(page, "ZZ-final-failure")
            log.error("Поток упал: %s\n%s", exc, traceback.format_exc())
            raise RuntimeError(f"Playwright: {exc}") from exc
        finally:
            browser.close()
