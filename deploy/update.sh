#!/usr/bin/env bash
# Deploy the latest code: pull, rebuild, run migrations, restart with minimal downtime.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose -f docker-compose.vps.yml build
docker compose -f docker-compose.vps.yml up -d
docker image prune -f >/dev/null
docker compose -f docker-compose.vps.yml ps
