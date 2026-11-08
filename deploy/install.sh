#!/bin/sh
# Install or update the systemd units on the Pi. Run from the repo as the normal user (uses sudo).
# Safe to re-run: the sync timer's on/off state and schedule are left as they are.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
USER_NAME=$(id -un)
UV=$(command -v uv || echo "$HOME/.local/bin/uv")
[ -x "$UV" ] || { echo "uv not found; install it first: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }

DB=$(grep -E '^GARMIN_DB=' "$REPO/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)
DB_DIR=$(dirname "${DB:-$REPO/data/garmin.db}")
[ -d "$DB_DIR" ] || { echo "Database folder $DB_DIR not found. Is the disk mounted?" >&2; exit 1; }

first_install=no
[ -f /etc/systemd/system/garmin-sync.timer ] || first_install=yes

for unit in garmin-dashboard.service garmin-sync.service garmin-sync.timer; do
  sed -e "s|@USER@|$USER_NAME|g" -e "s|@REPO@|$REPO|g" -e "s|@UV@|$UV|g" -e "s|@DB_DIR@|$DB_DIR|g" \
    "$REPO/deploy/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload

(cd "$REPO" && "$UV" sync --frozen)
sudo systemctl enable garmin-dashboard.service
sudo systemctl restart garmin-dashboard.service
[ "$first_install" = yes ] && sudo systemctl enable --now garmin-sync.timer

cp "$REPO/deploy/post-receive" "$REPO/.git/hooks/post-receive"
chmod +x "$REPO/.git/hooks/post-receive"

echo "Installed. Database folder: $DB_DIR. Timezone: $(timedatectl show -p Timezone --value)"
systemctl list-timers garmin-sync.timer --no-pager
