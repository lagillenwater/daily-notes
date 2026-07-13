#!/bin/bash
#
# Generate today's Zotero daily note, commit it, and push to GitHub.
# Invoked by the launchd agent (see com.lagillenwater.zotero-daily.plist).
#
# Exit codes are not propagated to launchd in a meaningful way, so all output
# is logged to logs/run.log for after-the-fact inspection.

set -euo pipefail

# Resolve the repo directory from this script's own location so it works on
# any machine regardless of username or checkout path.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer Homebrew python3 if present, otherwise fall back to whatever is on PATH.
PYTHON="$(command -v /opt/homebrew/bin/python3 || command -v python3)"
DATA_DIR="$HOME/Zotero"
OUT_DIR="$REPO_DIR/daily-notes"
LOG_DIR="$REPO_DIR/logs"

mkdir -p "$LOG_DIR"

# Timestamp every run so the log is readable.
echo "===== run $(date '+%Y-%m-%d %H:%M:%S %Z') ====="

cd "$REPO_DIR"

# Only run on weekdays. launchd may fire a missed job on wake, which could
# land on a weekend, so guard here as well as in the launchd schedule.
# %u gives the ISO weekday (1=Monday .. 7=Sunday).
DOW="$(date '+%u')"
if [ "$DOW" -gt 5 ]; then
    echo "Skipping: $(date '+%A') is not a weekday."
    exit 0
fi

TODAY="$(date '+%Y-%m-%d')"

# Generate the markdown for today into daily-notes/.
"$PYTHON" "$REPO_DIR/zotero_daily_notes.py" \
    --date "$TODAY" \
    --by added \
    --data-dir "$DATA_DIR" \
    --out-dir "$OUT_DIR"

OUT_FILE="$OUT_DIR/$TODAY.md"

# Sync with remote first so a manual push elsewhere does not cause a rejection.
git pull --rebase --autostash origin main || true

git add "$OUT_FILE"

# Only commit if something actually changed.
if git diff --cached --quiet; then
    echo "No changes to commit for $TODAY."
else
    git commit -m "Add Zotero daily note for $TODAY"
    git push origin main
    echo "Committed and pushed $TODAY.md"
fi
