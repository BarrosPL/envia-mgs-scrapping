from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


running = True


def stop(*_args: object) -> None:
    global running
    running = False


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    profile = Path(os.getenv("BROWSER_PROFILE_DIR", "/data/browser-data"))
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            headless=False,
            viewport={"width": 1280, "height": 850},
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        print("Navegador de login iniciado. Acesse o noVNC e autentique-se.", flush=True)
        while running and not page.is_closed():
            time.sleep(1)
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
