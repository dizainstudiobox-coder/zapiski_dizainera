"""Helper, который запускают ОДИН РАЗ на ноуте.

Открывает живой Chromium, даёт пользователю войти в Дзен руками,
после нажатия Enter сохраняет storage_state в dzen_state.json.

Использование:
    .venv/bin/python auth_dzen.py [out_path]

Файл dzen_state.json потом нужно scp-нуть на сервер в DZEN_STATE_PATH.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dzen_state.json")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(locale="ru-RU")
        page = ctx.new_page()
        page.goto("https://dzen.ru/", wait_until="domcontentloaded")
        print()
        print("=" * 60)
        print("  Войди в Дзен в открывшемся окне.")
        print("  После того как увидишь свой профиль автора — вернись")
        print("  в это окно терминала и нажми Enter.")
        print("=" * 60)
        input(">>> Enter, когда залогинился: ")
        ctx.storage_state(path=str(out))
        browser.close()
    print(f"Сохранено: {out.resolve()}")
    print("Теперь scp файл на сервер: scp", out, "root@213.148.5.51:/opt/projects/blog_dzen/code/dzen_state.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
