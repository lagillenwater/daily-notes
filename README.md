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
- `logs/` — run logs (git-ignored).

## Manual run

    ./run_daily.sh

Or generate a specific day without committing:

    python3 zotero_daily_notes.py --date 2026-06-23 --data-dir ~/Zotero --out-dir ./daily-notes

## Scheduling

The launchd agent is installed by symlinking (or copying) the plist into
`~/Library/LaunchAgents/` and loading it:

    cp com.lagillenwater.zotero-daily.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.lagillenwater.zotero-daily.plist

To stop it:

    launchctl unload ~/Library/LaunchAgents/com.lagillenwater.zotero-daily.plist
