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

# Install Google Chrome (real browser, not headless shell) + OS deps for Playwright
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
    libxml2-dev \
    libxslt1-dev \
    wget \
    xdg-utils \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

COPY scraper/ /app/scraper/
COPY requirements/scraper.txt /app/requirements/scraper.txt
COPY config/ /app/config/

RUN pip install --no-cache-dir -r /app/requirements/scraper.txt && playwright install-deps chromium

CMD ["python", "-u", "-m", "scraper.main"]