#!/bin/zsh
# daily.sh — launchd calls this every 30 min while the Mac is awake. It runs JobPilot's daily
# pipeline (run.py) at most once a day, and only when:
#   · you are using the Mac (keyboard/mouse in the last 5 min)
#   · it is plugged in, or the battery is at 50% or more
# Invisible by design: the agent Chrome runs headless (no window, no tabs on screen), so nothing
# shows up while you share your screen. If your visible agent Chrome is already open, filling is
# skipped (discovery, posting check and dashboard still run) so no tabs pop into it.
# Filled forms are saved as recipes: open the dashboard and "Open form" rebuilds them visibly.
cd "${0:A:h}"
ART=artifacts; mkdir -p $ART
STAMP=$ART/last-daily-run; LOCK=$ART/daily.lock
log() { echo "$(date '+%F %T') daily: $*"; }
st()  { echo "$(date '+%F %T')|$*" > $ART/daily-status; }                 # last decision, shown on the dashboard
conf() { plutil -extract $1 raw -o - settings.json 2>/dev/null || echo $2; } # dashboard-editable thresholds
MIN_BATT=$(conf min_battery 50); MAX_IDLE=$(( $(conf max_idle_min 5) * 60 ))
FORCE=${JOBPILOT_FORCE:-0}                                                 # dashboard "Run daily now": skip the gates

if [ $FORCE != 1 ]; then
[ "$(cat $STAMP 2>/dev/null)" = "$(date +%F)" ] && { st "done today — next run tomorrow"; exit 0; }

batt=$(pmset -g batt)
if ! print -r -- "$batt" | grep -q "AC Power"; then
  pct=$(print -r -- "$batt" | grep -oE '[0-9]+%' | head -1 | tr -d %)
  [ "${pct:-0}" -ge $MIN_BATT ] || { log "battery ${pct:-?}% on battery power — later"; st "skipped: battery ${pct:-?}% (needs $MIN_BATT% or power)"; exit 0; }
fi

idle=$(( $(ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {print $NF; exit}') / 1000000000 ))
[ "$idle" -lt $MAX_IDLE ] || { st "skipped: Mac idle $((idle/60)) min (runs only while you use it)"; exit 0; }
fi

# one run at a time (a run can take a while); a lock older than 3 h is a leftover from a crash
if ! mkdir $LOCK 2>/dev/null; then
  [ -n "$(find $LOCK -maxdepth 0 -mmin +180 2>/dev/null)" ] && rm -rf $LOCK && mkdir $LOCK || { st "skipped: a run is already going"; exit 0; }
fi
trap 'rm -rf $LOCK' EXIT; trap 'exit 143' TERM INT                     # a cancel from the dashboard still frees the lock

started=0
if ./browser.sh status >/dev/null 2>&1; then
  export JOBPILOT_SKIP_FILL=1                                              # your visible Chrome is up
  log "visible agent Chrome is open — skipping fill"
  [ $FORCE = 1 ] && unset JOBPILOT_SKIP_FILL                               # you asked for it: fill in the visible window
else
  ./browser.sh headless >/dev/null && started=1
fi

log "start (idle ${idle:-?}s, $(print -r -- "$batt" | grep -oE '[0-9]+%' | head -1))"
st "running${JOBPILOT_SKIP_FILL:+ (fill skipped: your agent Chrome window was open)}"
if .venv/bin/python run.py; then date +%F > $STAMP; st "ran${JOBPILOT_SKIP_FILL:+ — fill skipped: your agent Chrome window was open}"
else st "failed: run.py exited with an error (see run.log)"; fi
log "done (see run.log)"

[ $started = 1 ] && ./browser.sh stop >/dev/null                          # only stop what we started
