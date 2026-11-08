# Running the dashboard on the Raspberry Pi

The Pi runs the dashboard all the time and syncs from Garmin on a timer. The phone reaches it
through Tailscale, as before. Assumes a 64-bit OS and the repo at `~/garmin-dashboard`. Placeholders: `<pi>` is the Pi's SSH host,
`<db-folder>` the folder that holds the database (e.g. on an external disk; it must already exist).

## One-time setup

On the Mac, give the Pi a `Host <pi>` entry in `~/.ssh/config` (`HostName`, `User`, `IdentityFile`,
`IdentitiesOnly yes`), so git uses the right key.

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
git remote add pi <pi>:garmin-dashboard
git push pi main
scp .env <pi>:garmin-dashboard/          # then add GARMIN_DB=<db-folder>/garmin.db to it
scp data/garmin.db <pi>:<db-folder>/   # tags exist only here, so copy, don't re-sync
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
- Back up the database now and then, e.g. `sqlite3 <db-folder>/garmin.db ".backup garmin-backup.db"`.
