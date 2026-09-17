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

# A deploy that leaves the site 502ing should say so rather than look successful.
port=$(docker compose "${files[@]}" port nginx 80 2>/dev/null | awk -F: '{print $NF}')
if [ -n "$port" ]; then
  for _ in $(seq 1 15); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}/api/health" || true)
    if [ "$code" = "200" ]; then
      echo "OK: the app answers on port ${port}"
      exit 0
    fi
    sleep 2
  done
  echo "WARNING: the app did not answer 200 on port ${port} (last status: ${code:-none})" >&2
  exit 1
fi
echo "WARNING: nginx has no published port — the site cannot be reached from the host" >&2
exit 1
