from __future__ import annotations

import os
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def worker_command() -> list[str]:
    command = [
        "python",
        "app.py",
        "--database",
        "--send",
        "--yes",
        "--close-when-done",
        "--no-headless",
        "--profile-dir",
        os.getenv("BROWSER_PROFILE_DIR", "/data/browser-data"),
        "--limit",
        str(env_int("BATCH_SIZE", 15)),
        "--min-confidence",
        os.getenv("MIN_CONFIDENCE", "80"),
        "--min-delay",
        str(env_int("MIN_DELAY_SECONDS", 45)),
        "--max-delay",
        str(env_int("MAX_DELAY_SECONDS", 90)),
    ]
    if os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes"}:
        command.remove("--send")
        command.remove("--yes")
    return command


def notify(message: str) -> None:
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return
    request = urllib.request.Request(
        url,
        data=json.dumps({"text": message}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=10).close()
    except Exception as exc:
        print(f"Falha ao enviar alerta: {type(exc).__name__}", flush=True)


def next_run(now: datetime, run_time: str) -> datetime:
    hour, minute = (int(part) for part in run_time.split(":", 1))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def main() -> int:
    timezone = ZoneInfo(os.getenv("TZ", "America/Sao_Paulo"))
    run_time = os.getenv("RUN_TIME", "10:00")
    run_on_start = os.getenv("RUN_ON_START", "false").lower() in {"1", "true", "yes"}
    first = True
    while True:
        now = datetime.now(timezone)
        if not (first and run_on_start):
            scheduled = next_run(now, run_time)
            seconds = max(1, (scheduled - now).total_seconds())
            print(f"Próxima execução: {scheduled.isoformat()}", flush=True)
            time.sleep(seconds)
        first = False
        result = subprocess.run(worker_command(), check=False)
        print(f"Worker finalizado com código {result.returncode}", flush=True)
        if result.returncode == 3:
            notify("Instagram: sessão expirada ou verificação manual necessária.")
        elif result.returncode != 0:
            notify(f"Instagram worker falhou com código {result.returncode}.")


if __name__ == "__main__":
    raise SystemExit(main())
