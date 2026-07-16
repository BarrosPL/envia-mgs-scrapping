#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
mkdir -p "${BROWSER_PROFILE_DIR:-/data/browser-data}"

Xvfb "$DISPLAY" -screen 0 1280x900x24 -ac +extension GLX +render -noreset &
sleep 1
fluxbox >/tmp/fluxbox.log 2>&1 &

case "${APP_MODE:-worker}" in
  login)
    if [[ -z "${VNC_PASSWORD:-}" ]]; then
      echo "VNC_PASSWORD é obrigatória no modo login" >&2
      exit 2
    fi
    x11vnc -storepasswd "$VNC_PASSWORD" /tmp/vnc.pass >/dev/null
    x11vnc -display "$DISPLAY" -forever -shared -rfbauth /tmp/vnc.pass -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
    websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/novnc.log 2>&1 &
    exec python login_browser.py
    ;;
  worker)
    exec python scheduler.py
    ;;
  once)
    exec python app.py --database --send --yes --close-when-done --no-headless \
      --profile-dir "${BROWSER_PROFILE_DIR:-/data/browser-data}" \
      --limit "${BATCH_SIZE:-15}" --min-confidence "${MIN_CONFIDENCE:-80}" \
      --min-delay "${MIN_DELAY_SECONDS:-45}" --max-delay "${MAX_DELAY_SECONDS:-90}"
    ;;
  *)
    echo "APP_MODE deve ser login, worker ou once" >&2
    exit 2
    ;;
esac
