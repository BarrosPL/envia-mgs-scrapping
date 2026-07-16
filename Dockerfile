FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BROWSER_PROFILE_DIR=/data/browser-data \
    TZ=America/Sao_Paulo \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       fluxbox novnc python3.12-venv websockify x11vnc x11-xkb-utils xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt
COPY . .
RUN chmod +x /app/docker-entrypoint.sh && mkdir -p /data/browser-data

EXPOSE 6080
VOLUME ["/data"]
ENTRYPOINT ["/app/docker-entrypoint.sh"]
