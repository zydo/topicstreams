#!/usr/bin/env sh
# Out-of-the-box startup: create .env from its template if missing (Docker
# Compose reads .env on the host before any container starts, so this cannot
# happen inside the app like the YAML config auto-copy does), then start the
# stack. Extra arguments are passed to `docker compose up`.
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example (using default settings)"
fi

exec docker compose up -d "$@"
