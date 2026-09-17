#!/usr/bin/env bash
# Deploy the latest code: pull, rebuild, run migrations, restart with minimal downtime.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only

# An explicit -f stops Compose auto-loading docker-compose.override.yml, which is where this host
# publishes nginx on 127.0.0.1:8090 and disables caddy. Dropping it unpublishes the port and the
# site answers 502, so pass the override too whenever it exists.
files=(-f docker-compose.vps.yml)
[ -f docker-compose.override.yml ] && files+=(-f docker-compose.override.yml)

docker compose "${files[@]}" build
docker compose "${files[@]}" up -d
docker image prune -f >/dev/null
docker compose "${files[@]}" ps
