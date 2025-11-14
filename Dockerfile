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

# Dependencies for Playwright
RUN apt-get update && apt-get install -y \
    ca-certificates \
    fonts-liberation \
    gnupg \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    wget \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

COPY scraper/ /app/scraper/
COPY requirements/scraper.txt /app/requirements/scraper.txt

RUN pip install --no-cache-dir -r /app/requirements/scraper.txt && playwright install chromium

CMD ["python", "-u", "-m", "scraper.main"]