#!/usr/bin/env bash
# The agent's browser: a dedicated Chrome profile with remote debugging on :9222.
#   browser.sh start   — launch it (headed). Log into LinkedIn/Wellfound/Google ONCE in this window.
#   browser.sh headless — same, but invisible (no window): used by daily.sh
#   browser.sh status  — is it up?
#   browser.sh stop    — kill it
# Chrome 136+ refuses CDP on the default profile, hence the separate --user-data-dir.
# The browser outlives every agent run, so filled application tabs stay open for you to Submit.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ART="$HERE/artifacts"; mkdir -p "$ART"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="$HOME/.jobpilot-chrome"
PORT=9222

# headless: no window, no tabs on screen (the daily background run). Same profile, so logins work.
# Sends the normal Chrome user agent: "HeadlessChrome" gets flagged as a bot by some job sites.
EXTRA=()
START_URL="http://localhost:8765/"   # the dashboard lives in this window, next to the form tabs it opens
if [ "${1:-}" = "headless" ]; then
  START_URL="about:blank"
  MAJOR=$("$CHROME" --version | grep -oE '[0-9]+' | head -1)
  EXTRA=(--headless=new "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/$MAJOR.0.0.0 Safari/537.36")
fi

case "${1:-status}" in
  start|headless)
    if curl -s -m 2 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
      echo "already running on :$PORT"; exit 0; fi
    # each agent job gets its own background window (quickfill/cdp-proxy.mjs); --disable-backgrounding-occluded-windows
    # keeps those painting while they sit behind your windows — otherwise dropdown clicks time out.
    # Hidden TABS are throttled as normal, so old filled forms don't burn CPU.
    # --enable-unsafe-extension-debugging: lets apply_agent load chrome-helper/ over CDP (Extensions.loadUnpacked),
    # which moves finished forms into your review window. Any extension-limiting flag (--disable-extensions,
    # --disable-extensions-except) also blocks the helper, so the synced extensions are switched OFF in this
    # profile instead (chrome://extensions) — they'd run on every form and bloat the agent's snapshots.
    nohup "$CHROME" --remote-debugging-port=$PORT --user-data-dir="$PROFILE" \
      --no-first-run --no-default-browser-check --enable-unsafe-extension-debugging \
      --disable-backgrounding-occluded-windows ${EXTRA[@]+"${EXTRA[@]}"} "$START_URL" >"$ART/chrome.log" 2>&1 &
    echo $! > "$ART/chrome.pid"
    for i in $(seq 1 20); do
      curl -s -m 1 "http://localhost:$PORT/json/version" >/dev/null 2>&1 && {
        echo "started (pid $(cat "$ART/chrome.pid"))"
        # the JobPilot extension (review-window tabs + resume side panel): loading over CDP lasts until Chrome quits
        "$HERE/.venv/bin/python" -c 'import apply_agent; apply_agent.load_helper()' >/dev/null 2>&1 \
          && echo "JobPilot extension loaded" || echo "JobPilot extension NOT loaded (see apply_agent.load_helper)"
        exit 0; }
      sleep 0.5
    done
    echo "chrome did not come up — see $ART/chrome.log"; exit 1 ;;
  status)
    v=$(curl -s -m 2 "http://localhost:$PORT/json/version" | python3 -c 'import sys,json;print(json.load(sys.stdin)["Browser"])' 2>/dev/null) \
      && echo "up: $v" || { echo "not running — run: $0 start"; exit 1; } ;;
  stop)
    # find it by profile dir, not the pid file: Chrome relaunches itself after auto-updates (new pid)
    # the MAIN process only — helper/renderer processes carry --user-data-dir too
    pid=$(pgrep -f -- "Contents/MacOS/Google Chrome --.*--user-data-dir=$PROFILE" | head -1)
    [ -n "$pid" ] && kill "$pid" && rm -f "$ART/chrome.pid" && echo "stopped (pid $pid)" || echo "not running" ;;
  *) echo "usage: $0 start|headless|status|stop"; exit 2 ;;
esac
