#!/bin/sh
# Turn the scheduled Garmin sync on or off, or change when it runs. Run on the Pi.
#   deploy/sync-timer.sh status
#   deploy/sync-timer.sh on | off
#   deploy/sync-timer.sh at 09:00                 one time a day
#   deploy/sync-timer.sh at 07:30 19:00           several times a day
#   deploy/sync-timer.sh at '*-*-* 0/6:00'        any systemd OnCalendar value (here: every 6 hours)
#   deploy/sync-timer.sh default                  back to the schedule in deploy/garmin-sync.timer
#   deploy/sync-timer.sh now                      run a sync now and show its log
set -eu
TIMER=garmin-sync.timer
DROPIN=/etc/systemd/system/$TIMER.d/schedule.conf
[ -f "/etc/systemd/system/$TIMER" ] || { echo "$TIMER isn't installed yet: run 'sh deploy/install.sh' first" >&2; exit 1; }

case "${1:-status}" in
  status)
    systemctl is-enabled "$TIMER" || true
    systemctl list-timers "$TIMER" --no-pager
    echo; journalctl -u garmin-sync.service -n 15 --no-pager ;;
  on)  sudo systemctl enable --now "$TIMER" ;;
  off) sudo systemctl disable --now "$TIMER" ;;
  at)
    shift
    [ $# -gt 0 ] || { echo "Give at least one time, e.g. 09:00" >&2; exit 1; }
    conf="[Timer]
OnCalendar="
    for t in "$@"; do
      systemd-analyze calendar "$t" >/dev/null || { echo "Not a valid time: $t" >&2; exit 1; }
      conf="$conf
OnCalendar=$t"
    done
    sudo mkdir -p "$(dirname "$DROPIN")"
    printf '%s\n' "$conf" | sudo tee "$DROPIN" >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl restart "$TIMER"
    systemctl list-timers "$TIMER" --no-pager ;;
  default)
    sudo rm -f "$DROPIN"
    sudo systemctl daemon-reload
    sudo systemctl restart "$TIMER"
    systemctl list-timers "$TIMER" --no-pager ;;
  now)
    sudo systemctl start garmin-sync.service
    journalctl -u garmin-sync.service -n 15 --no-pager ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
