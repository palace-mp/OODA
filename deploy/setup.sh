#!/usr/bin/env bash
# Sets up OODA on a fresh Ubuntu 24.04 (or Debian 12) server.
#
#   curl -fsSL https://raw.githubusercontent.com/palace-mp/ooda/main/deploy/setup.sh -o setup.sh
#   sudo bash setup.sh
#
# Safe to run again: it keeps an existing .env and just updates and restarts.

set -euo pipefail

REPO="${REPO:-https://github.com/palace-mp/ooda.git}"
DIR="${DIR:-/opt/ooda}"
BACKUPS="/var/backups/ooda"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask() { # ask VAR "Question" "default"
  local answer
  read -r -p "$2${3:+ [$3]}: " answer </dev/tty || true
  printf -v "$1" '%s' "${answer:-$3}"
}
secret() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "${1:-50}"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

say "1/6  Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl git openssl ufw unattended-upgrades
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  # Docker's own packages; Ubuntu's docker.io lacks the compose plugin on some releases.
  install -m 0755 -d /etc/apt/keyrings
  . /etc/os-release
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker >/dev/null

say "2/6  Firewall: only SSH, HTTP and HTTPS"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null
# Install security updates automatically.
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null

say "3/6  Fetching the code into $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO" "$DIR"
fi
cd "$DIR"

say "4/6  Configuration"
if [ -f .env ]; then
  echo "Keeping the existing $DIR/.env"
else
  ask DOMAIN "Domain" "ooda.obrydd.com"
  ask FROM "Send e-mail from" "OODA <ooda@${DOMAIN#*.}>"
  echo "E-mail (SMTP). Leave empty to fill in later in $DIR/.env."
  ask SMTP_HOST "  SMTP server, e.g. smtp.postmarkapp.com" ""
  ask SMTP_USER "  SMTP user" ""
  ask SMTP_PASS "  SMTP password" ""
  cat > .env <<EOF
DOMAIN=$DOMAIN
SITE_URL=https://$DOMAIN
ALLOWED_HOSTS=$DOMAIN
CSRF_TRUSTED_ORIGINS=https://$DOMAIN
SECRET_KEY=$(secret 50)
SETUP_KEY=$(secret 24)
ALLOW_SIGNUP=false
TIME_ZONE=Europe/Stockholm
DEFAULT_FROM_EMAIL='$FROM'
EMAIL_HOST=$SMTP_HOST
EMAIL_PORT=587
EMAIL_HOST_USER=$SMTP_USER
EMAIL_HOST_PASSWORD='$SMTP_PASS'
EMAIL_USE_TLS=true
EOF
  chmod 600 .env
  echo "Wrote $DIR/.env"
fi
DOMAIN="$(grep '^DOMAIN=' .env | cut -d= -f2-)"
SETUP_KEY="$(grep '^SETUP_KEY=' .env | cut -d= -f2-)"

say "5/6  Building and starting (the first build takes a few minutes)"
docker compose up -d --build
docker image prune -f >/dev/null

say "6/6  Nightly backups to $BACKUPS (kept 14 days)"
mkdir -p "$BACKUPS"
cat > /etc/cron.daily/ooda-backup <<EOF
#!/bin/sh
set -e
cd $DIR
docker compose exec -T web python -c "import sqlite3; s=sqlite3.connect('/data/ooda.sqlite3'); d=sqlite3.connect('/data/backup.sqlite3'); s.backup(d); d.close()"
docker compose cp web:/data/backup.sqlite3 $BACKUPS/ooda-\$(date +%F).sqlite3
find $BACKUPS -name 'ooda-*.sqlite3' -mtime +14 -delete
EOF
chmod 755 /etc/cron.daily/ooda-backup

IP="$(curl -fsS -4 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
say "Done."
cat <<EOF

  The server's address is $IP. Point DNS for $DOMAIN to it (an A record).
  Caddy fetches the HTTPS certificate by itself once DNS works; give it a minute.

  Then create your owner account here (only works once, while no users exist):

      https://$DOMAIN/start/?key=$SETUP_KEY

  Useful commands, run in $DIR:
      docker compose ps                 what is running
      docker compose logs -f web        application log
      docker compose logs caddy         HTTPS/certificate problems
      sudo bash deploy/update.sh        fetch the latest version and restart
      nano .env, then: docker compose up -d     change settings (e.g. e-mail)

EOF
