from __future__ import annotations

import os
import signal
import subprocess
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
        executable = playwright.chromium.executable_path
    process = subprocess.Popen(
        [
            executable,
            f"--user-data-dir={profile}",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--lang=pt-BR",
            "--window-size=1280,850",
            "https://www.instagram.com/",
        ]
    )
    print("Navegador de login iniciado. Acesse o noVNC e autentique-se.", flush=True)
    while running and process.poll() is None:
        time.sleep(1)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
