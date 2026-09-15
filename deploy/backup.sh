#!/usr/bin/env bash
# Compressed PostgreSQL backup into ./backups, keeping the last 14 days.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
stamp="$(date +%Y%m%d-%H%M)"
docker compose -f docker-compose.vps.yml exec -T postgres pg_dump -U voiceagent voiceagent | gzip > "backups/voiceagent-$stamp.sql.gz"
find backups -name 'voiceagent-*.sql.gz' -mtime +14 -delete
echo "Backup written: backups/voiceagent-$stamp.sql.gz"
