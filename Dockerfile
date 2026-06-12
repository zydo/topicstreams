# ========= Shared Setup =========

FROM python:3.14-slim AS base
WORKDIR /app

ENV PYTHONPATH=/app

COPY common/ /app/common

# ========= API Image =========

FROM base AS api

COPY api/ /app/api/
COPY requirements/api.txt /app/requirements/api.txt

RUN pip install --no-cache-dir -r /app/requirements/api.txt

CMD ["python", "-m", "api.main"]

# ========= Scraper Image =========

FROM base AS scraper

# Playwright's bundled Chromium, NOT google-chrome-stable: Chrome has no
# Linux arm64 build, which forced amd64 emulation (Rosetta 2) on Apple
# Silicon — and Google's bot detection CAPTCHAs emulated browsers on /search
# (verified 2026-06-11: identical setup passes natively, fails under
# emulation). Bundled Chromium runs native on both arches.
RUN apt-get update && apt-get install -y ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY scraper/ /app/scraper/
COPY requirements/scraper.txt /app/requirements/scraper.txt
COPY config/ /app/config/

RUN pip install --no-cache-dir -r /app/requirements/scraper.txt \
    && playwright install --with-deps chromium

CMD ["python", "-u", "-m", "scraper.main"]