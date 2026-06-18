# ========= Shared Setup =========

FROM python:3.14-slim AS base
WORKDIR /app

ENV PYTHONPATH=/app

# pip >= 25.1 is required for `pip install --group` (PEP 735 dependency groups).
# Install deps before copying app code so the deps layer caches across code edits.
RUN pip install --no-cache-dir --upgrade pip
COPY pyproject.toml /app/pyproject.toml

# ========= API Image =========

FROM base AS api

RUN pip install --no-cache-dir --group api

COPY common/ /app/common
COPY api/ /app/api/
# config.yml holds the unified config (scraper/anti-detection/api sections).
# Bake the template and, if present, the local runtime config.yml (gitignored,
# like the old config/ dir) so the build picks up local customizations.
COPY config.yml* /app/

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

RUN pip install --no-cache-dir --group scraper \
    && playwright install --with-deps chromium

COPY common/ /app/common
COPY scraper/ /app/scraper/
COPY config.yml* /app/

CMD ["python", "-u", "-m", "scraper.main"]
