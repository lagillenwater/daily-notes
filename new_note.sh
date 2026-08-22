#!/bin/bash
#
# Create (or reopen) a one-off note for a specific topic.
#
# Notes live at topics/<slug>/YYYY-MM-DD.md, where <slug> is the topic name
# lowercased with spaces turned to hyphens and non-alphanumeric characters
# stripped. Multiple entries for the same topic accumulate as separate dated
# files under that topic's directory. Unlike the daily Zotero notes, nothing
# here is committed or pushed automatically.
#
# Usage:
#   ./new_note.sh "grant proposal ideas"

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 \"<topic name>\"" >&2
    exit 1
fi

TOPIC="$*"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SLUG="$(echo "$TOPIC" | tr '[:upper:]' '[:lower:]' | tr -s ' ' '-' | tr -dc 'a-z0-9-')"

if [ -z "$SLUG" ]; then
    echo "Topic name must contain at least one letter or number." >&2
    exit 1
fi

TOPIC_DIR="$REPO_DIR/topics/$SLUG"
TODAY="$(date '+%Y-%m-%d')"
NOTE_FILE="$TOPIC_DIR/$TODAY.md"

mkdir -p "$TOPIC_DIR"

if [ ! -f "$NOTE_FILE" ]; then
    printf '# %s\n_%s_\n\n' "$TOPIC" "$TODAY" > "$NOTE_FILE"
    echo "Created $NOTE_FILE"
else
    echo "Reopening existing $NOTE_FILE"
fi

"${EDITOR:-vi}" "$NOTE_FILE"
