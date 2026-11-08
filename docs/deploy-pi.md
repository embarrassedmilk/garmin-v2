# Running the dashboard on the Raspberry Pi

The Pi runs the dashboard all the time and syncs from Garmin on a timer. The phone reaches it
through Tailscale, as before. Assumes a 64-bit OS, user `pi`, the repo at `~/garmin-dashboard`
and the database on the HDD at `/mnt/mediavol1/garmin/`.

## One-time setup

On the Mac, `~/.ssh/config` has a `rasp` host (192.168.178.20, user `pi`, key `~/.ssh/rasp_rsa`).

On the Pi:
```sh
sudo apt install git
curl -LsSf https://astral.sh/uv/install.sh | sh
git init -b main ~/garmin-dashboard
git -C ~/garmin-dashboard config receive.denyCurrentBranch updateInstead
timedatectl                      # the time zone must be mine: it decides which day "today" is
```

On the Mac:
```sh
git remote add pi rasp:garmin-dashboard
git push pi main
scp .env rasp:garmin-dashboard/          # then add GARMIN_DB=/mnt/mediavol1/garmin/garmin.db to it
scp data/garmin.db rasp:/mnt/mediavol1/garmin/   # tags exist only here, so copy, don't re-sync
```

On the Pi:
```sh
cd ~/garmin-dashboard && sh deploy/install.sh
sudo tailscale serve --bg 8000           # never funnel: the dashboard has no login
```
`install.sh` installs the systemd units, starts the dashboard, turns on the daily sync (first
install only) and installs the push hook. It needs passwordless sudo, which Raspberry Pi OS gives
the first user.

Then, on the Mac, stop using the local copy (`tailscale serve reset`), or the two databases drift
apart. The phone's address changes to the Pi's Tailscale name, so re-add the home-screen app.

## Deploying changes

`git push pi main`. The hook runs `uv sync` and restarts the dashboard; if anything in `deploy/`
changed, it re-runs `install.sh` instead.

## Scheduled sync

Daily at 09:00 by default (after the watch has usually synced the night). If the Pi was off, it
runs at the next boot. The Sync button and `uv run python -m backend.sync` still work.
```sh
deploy/sync-timer.sh status            # on or off, next run, last log lines
deploy/sync-timer.sh off               # or: on
deploy/sync-timer.sh at 07:30 19:00    # one or more times a day; any OnCalendar value works
deploy/sync-timer.sh default           # back to 09:00
deploy/sync-timer.sh now               # sync now
```
Sync errors show in the dashboard's banner, as with a manual sync.

## Logs and troubleshooting
- `journalctl -u garmin-dashboard -f`, `journalctl -u garmin-sync`
- Both units wait for the database's disk (`RequiresMountsFor`). If `GARMIN_DB`'s folder is
  missing, the app stops with "Is the disk mounted?" rather than starting an empty database.
- Back up `/mnt/mediavol1/garmin/garmin.db` now and then, e.g.
  `sqlite3 /mnt/mediavol1/garmin/garmin.db ".backup garmin-backup.db"`.
