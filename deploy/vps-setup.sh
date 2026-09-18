#!/usr/bin/env bash
# One-time setup of Samvaad AI on a fresh Ubuntu VPS (Hostinger KVM 2 or similar).
#
#   git clone https://github.com/Devpysber/AI_Sales_Calling_Agent.git /opt/psyber-voice
#   cd /opt/psyber-voice && sudo bash deploy/vps-setup.sh
#
# Before running: point your domain's A record to this server's IP.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

if [[ $EUID -ne 0 ]]; then echo "Run as root: sudo bash deploy/vps-setup.sh"; exit 1; fi

echo "==> Installing Docker and firewall"
if ! command -v docker >/dev/null; then
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg ufw
  curl -fsSL https://get.docker.com | sh
fi
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443 >/dev/null
ufw --force enable >/dev/null

echo "==> Swap (keeps builds from running out of memory)"
if ! swapon --show | grep -q swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Configuration"
if [[ ! -f .env ]]; then
  cp .env.example .env
  read -rp "Domain for this server (e.g. voice.yourcompany.com): " DOMAIN
  read -rp "Email for HTTPS certificates: " ACME_EMAIL
  read -rp "Admin sign-in email: " ADMIN_EMAIL
  read -rsp "Admin password (min 10 chars): " ADMIN_PASSWORD; echo
  set_env() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
  set_env DOMAIN "$DOMAIN"
  set_env ACME_EMAIL "$ACME_EMAIL"
  set_env PUBLIC_BASE_URL "https://$DOMAIN"
  set_env ENVIRONMENT production
  set_env ADMIN_EMAIL "$ADMIN_EMAIL"
  set_env ADMIN_PASSWORD "$ADMIN_PASSWORD"
  set_env SECRET_KEY "$(openssl rand -hex 32)"
  set_env POSTGRES_PASSWORD "$(openssl rand -hex 24)"
  set_env PLIVO_VALIDATE_SIGNATURE true
  echo
  echo "Now add your provider keys to $APP_DIR/.env:"
  echo "  PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_PHONE_NUMBER, SARVAM_API_KEY, OPENROUTER_API_KEY, RESEND_API_KEY, EMAIL_FROM"
  read -rp "Press Enter when .env is complete..."
fi

set -a; source .env; set +a
for key in DOMAIN SECRET_KEY POSTGRES_PASSWORD ADMIN_PASSWORD PLIVO_AUTH_ID SARVAM_API_KEY; do
  if [[ -z "${!key:-}" ]]; then echo "Missing $key in .env"; exit 1; fi
done

echo "==> Checking DNS for $DOMAIN"
SERVER_IP="$(curl -s https://api.ipify.org || true)"
DNS_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
if [[ -n "$SERVER_IP" && "$DNS_IP" != "$SERVER_IP" ]]; then
  echo "Warning: $DOMAIN resolves to '${DNS_IP:-nothing}', this server is $SERVER_IP. HTTPS will fail until DNS points here."
fi

echo "==> Building and starting (first build takes a few minutes)"
docker compose -f docker-compose.vps.yml up -d --build

echo "==> Daily database backup (03:30, keeps 14 days)"
mkdir -p backups
cat > /etc/cron.d/psyber-voice-backup <<CRON
30 3 * * * root cd $APP_DIR && bash deploy/backup.sh >> /var/log/psyber-voice-backup.log 2>&1
CRON

echo
echo "Done. Open https://$DOMAIN and sign in with $ADMIN_EMAIL."
echo "Then: Integrations & system → Inbound calls → Reconnect, so Plivo uses https://$DOMAIN."
