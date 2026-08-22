# daily-notes

Automated daily aggregation of Zotero notes and PDF annotations.

Every day at 6:00 PM, a launchd agent runs `zotero_daily_notes.py`, writes that
day's notes and annotations to `daily-notes/YYYY-MM-DD.md`, then commits and
pushes the result to GitHub.

## Layout

- `zotero_daily_notes.py` — aggregates Zotero notes/annotations for a given day.
- `run_daily.sh` — wrapper that generates the note, commits, and pushes.
- `com.lagillenwater.zotero-daily.plist` — launchd schedule (6pm daily).
- `daily-notes/` — generated Markdown, one file per day.
- `new_note.sh` — creates/opens a one-off note for a specific topic.
- `topics/` — manual Markdown notes, one directory per topic.
- `logs/` — run logs (git-ignored).

## Manual run

    ./run_daily.sh

Or generate a specific day without committing:

    python3 zotero_daily_notes.py --date 2026-06-23 --data-dir ~/Zotero --out-dir ./daily-notes

## Topic notes

For one-off notes on a specific topic (not tied to a day), use `new_note.sh`:

    ./new_note.sh "grant proposal ideas"

This creates (or reopens, if run again the same day) `topics/grant-proposal-ideas/YYYY-MM-DD.md`
and opens it in `$EDITOR`. Unlike the daily notes, topic notes are never
committed or pushed automatically — commit them yourself whenever you like.
