#!/usr/bin/env python3
"""Aggregate Zotero notes AND PDF annotations for a given day into one Markdown file.

Reads a temporary copy of zotero.sqlite (never the live DB, which Zotero locks)
and writes one Markdown file per day. Entries are grouped by the document they
belong to. Two kinds of entry appear:

  - Notes        (itemNotes): standalone notes and notes attached to items.
  - Annotations  (itemAnnotations): highlights/underlines made in the PDF reader,
                 shown with the highlighted passage and your comment (if any).

Why both: on a typical reading day you mark up PDFs rather than write notes, and
those highlights live in a separate table. A daily aggregator that reads only
itemNotes silently drops that work.

Usage:
    python3 zotero_daily_notes.py                  # today, local timezone
    python3 zotero_daily_notes.py --date 2026-06-23
    python3 zotero_daily_notes.py --by added       # bucket by creation date
    python3 zotero_daily_notes.py --notes-only     # skip annotations
    python3 zotero_daily_notes.py --data-dir ~/Zotero --out-dir ~/zotero-daily
"""

import argparse
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path


# Zotero annotation type codes -> readable label.
ANNOTATION_TYPES = {1: "highlight", 2: "note", 3: "image", 4: "ink", 5: "underline"}


# ---------- HTML -> plain text (notes are stored as HTML) ----------

class _NoteTextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
              "ul", "ol", "blockquote", "tr"}

    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self._parts.append("\n- ")
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        self._parts.append(data)

    def text(self):
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(note_html):
    if not note_html:
        return ""
    p = _NoteTextExtractor()
    p.feed(note_html)
    return p.text()


def first_line_title(text, fallback="(untitled)"):
    for line in text.splitlines():
        line = line.strip().lstrip("- ").strip()
        if line:
            return line[:120]
    return fallback


# ---------- DB access ----------

def find_zotero_db(data_dir):
    candidate = Path(data_dir).expanduser() / "zotero.sqlite"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"zotero.sqlite not found in {data_dir}. "
        "Pass --data-dir pointing at your Zotero data directory "
        "(Zotero > Settings > Advanced > Files and Folders)."
    )


def open_safe_copy(db_path):
    """Copy the DB (and -wal/-shm sidecars) to temp and open read-only."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite", prefix="zotero_copy_")
    os.close(tmp_fd)
    shutil.copy2(db_path, tmp_path)
    for ext in ("-wal", "-shm"):
        side = Path(str(db_path) + ext)
        if side.exists():
            shutil.copy2(side, tmp_path + ext)
    conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
    return conn, tmp_path


def cleanup(tmp_path):
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(tmp_path + ext)
        except OSError:
            pass


# ---------- time window ----------

def day_window_utc(date_str, use_utc):
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    if use_utc:
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    else:
        local_tz = datetime.now().astimezone().tzinfo
        start = datetime(day.year, day.month, day.day, tzinfo=local_tz).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt)


# Pull one named field's value for an item from the itemData structure.
FIELD_Q = """
SELECT iv.value
FROM itemData id_
JOIN fields f ON f.fieldID = id_.fieldID AND f.fieldName = ?
JOIN itemDataValues iv ON iv.valueID = id_.valueID
WHERE id_.itemID = ?
LIMIT 1;
"""

# Author surnames in order, editors excluded. fieldMode=1 marks a single-field
# (institutional) name where the org is stored in lastName and firstName is null.
AUTHORS_Q = """
SELECT cr.lastName, cr.firstName, cr.fieldMode
FROM itemCreators ic
JOIN creators cr ON cr.creatorID = ic.creatorID
JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID
WHERE ic.itemID = ? AND ct.creatorType = 'author'
ORDER BY ic.orderIndex;
"""

# Item type name, used to skip over attachments when resolving the document.
ITEMTYPE_Q = """
SELECT it.typeName
FROM items i
JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
WHERE i.itemID = ?;
"""

# One parent hop. A note/annotation/attachment each store their parent in their
# own table; whichever applies, we return it.
PARENT_Q = """
SELECT COALESCE(an.parentItemID, no.parentItemID, att.parentItemID)
FROM items i
LEFT JOIN itemAnnotations an ON an.itemID = i.itemID
LEFT JOIN itemNotes       no ON no.itemID = i.itemID
LEFT JOIN itemAttachments att ON att.itemID = i.itemID
WHERE i.itemID = ?;
"""


def _itemtype(conn, item_id):
    row = conn.execute(ITEMTYPE_Q, (item_id,)).fetchone()
    return row[0] if row else None


def _parent(conn, item_id):
    row = conn.execute(PARENT_Q, (item_id,)).fetchone()
    return row[0] if row and row[0] is not None else None


def doc_item_for_item(conn, item_id):
    """Climb to the bibliographic document an entry belongs to.

    The right target is the first ancestor that is NOT an attachment, because a
    standalone PDF attachment can itself carry a 'title' field (often the literal
    string 'PDF'). Stopping at the first titled item would mislabel the group and
    miss the authors/date, which live on the parent work, not the attachment.

    If the chain dead-ends at an attachment with no further parent, we return
    that attachment so the entry still groups under something, but it will have
    no citation because attachments carry no creators.
    """
    cur = item_id
    last_seen = None
    for _ in range(6):  # guard against cycles / pathological depth
        parent = _parent(conn, cur)
        if parent is None:
            break
        cur = parent
        last_seen = cur
        if _itemtype(conn, cur) != "attachment":
            return cur  # first non-attachment ancestor: the real document
    return last_seen  # only attachments above; group under the nearest one


def _field(conn, item_id, field_name):
    if item_id is None:
        return None
    row = conn.execute(FIELD_Q, (field_name, item_id)).fetchone()
    return row[0] if row else None


def _year(date_value):
    # Zotero stores dates like '2023-08-00 2023-08' or free text. Take the first
    # plausible four-digit year (19xx/20xx) rather than trusting the format.
    if not date_value:
        return None
    m = re.search(r"\b(19|20)\d{2}\b", date_value)
    return m.group(0) if m else None


def title_for_item(conn, item_id):
    doc_id = doc_item_for_item(conn, item_id)
    return _field(conn, doc_id, "title") if doc_id else None


def citation_for_doc(conn, doc_id):
    """Build a short reference line for a resolved document item.

    Format: 'Lastname et al. (Year). Container'. Pieces are omitted gracefully
    when absent, so a preprint with no journal still yields 'Lastname (2024)'.
    Returns None for standalone/orphan entries with no parent document.
    """
    if not doc_id:
        return None

    authors = conn.execute(AUTHORS_Q, (doc_id,)).fetchall()
    if authors:
        first_last = (authors[0][0] or "").strip()
        if len(authors) == 1:
            author_str = first_last
        elif len(authors) == 2:
            second_last = (authors[1][0] or "").strip()
            author_str = "{} & {}".format(first_last, second_last)
        else:
            author_str = "{} et al.".format(first_last)
    else:
        author_str = None

    year = _year(_field(conn, doc_id, "date"))
    container = (_field(conn, doc_id, "publicationTitle")
                or _field(conn, doc_id, "bookTitle")
                or _field(conn, doc_id, "proceedingsTitle")
                or _field(conn, doc_id, "publisher"))

    head = author_str or ""
    if year:
        head = "{} ({})".format(head, year).strip()
    parts = [p for p in (head.strip(), (container or "").strip()) if p]
    return ". ".join(parts) if parts else None


# ---------- queries ----------

NOTES_Q = """
SELECT n.itemID, n.note, i.dateAdded, i.dateModified
FROM itemNotes n
JOIN items i ON i.itemID = n.itemID
LEFT JOIN deletedItems d ON d.itemID = n.itemID
WHERE d.itemID IS NULL AND i.{tcol} >= ? AND i.{tcol} < ?;
"""

ANNOT_Q = """
SELECT a.itemID, a.type, a.text, a.comment, a.pageLabel, a.color,
       i.dateAdded, i.dateModified
FROM itemAnnotations a
JOIN items i ON i.itemID = a.itemID
LEFT JOIN deletedItems d ON d.itemID = a.itemID
WHERE d.itemID IS NULL AND a.isExternal = 0
  AND i.{tcol} >= ? AND i.{tcol} < ?;
"""


def fetch_entries(conn, start, end, by, include_annotations):
    tcol = "dateModified" if by == "modified" else "dateAdded"
    entries = []

    for r in conn.execute(NOTES_Q.format(tcol=tcol), (start, end)):
        item_id, note_html, d_add, d_mod = r
        text = html_to_text(note_html)
        doc_id = doc_item_for_item(conn, item_id)
        group = (_field(conn, doc_id, "title") if doc_id else None) or "Standalone notes (no parent)"
        entries.append({
            "kind": "note",
            "group": group,
            "citation": citation_for_doc(conn, doc_id),
            "title": first_line_title(text, "(untitled note)"),
            "body": text or "_(empty note)_",
            "stamp": d_mod if by == "modified" else d_add,
            "meta": "",
        })

    if include_annotations:
        for r in conn.execute(ANNOT_Q.format(tcol=tcol), (start, end)):
            item_id, atype, atext, comment, page, color, d_add, d_mod = r
            label = ANNOTATION_TYPES.get(atype, "type {}".format(atype))
            quote = (atext or "").strip()
            comment = (comment or "").strip()
            # The highlighted passage is the content; the comment is your gloss.
            body_parts = []
            if quote:
                body_parts.append("> " + quote.replace("\n", "\n> "))
            if comment:
                body_parts.append(comment)
            body = "\n\n".join(body_parts) if body_parts else "_(empty annotation)_"
            title = first_line_title(comment or quote, "(annotation)")
            meta = label + (", p. {}".format(page) if page else "")
            doc_id = doc_item_for_item(conn, item_id)
            group = (_field(conn, doc_id, "title") if doc_id else None) or "Orphan annotations"
            entries.append({
                "kind": "annotation",
                "group": group,
                "citation": citation_for_doc(conn, doc_id),
                "title": title,
                "body": body,
                "stamp": d_mod if by == "modified" else d_add,
                "meta": meta,
            })

    entries.sort(key=lambda e: ((e["group"] or "").lower(), e["stamp"]))
    return entries


# ---------- render ----------

def render_markdown(entries, date_str, by):
    verb = "modified" if by == "modified" else "created"
    lines = ["# Zotero notes and annotations for {}".format(date_str), ""]
    if not entries:
        lines.append("_Nothing {} on this day._".format(verb))
        return "\n".join(lines) + "\n"

    n_notes = sum(1 for e in entries if e["kind"] == "note")
    n_annot = sum(1 for e in entries if e["kind"] == "annotation")
    lines.append("_{} note(s), {} annotation(s), grouped by document._".format(n_notes, n_annot))
    lines.append("")

    current = object()
    for e in entries:
        if e["group"] != current:
            current = e["group"]
            lines += ["## {}".format(e["group"]), ""]
            if e.get("citation"):
                lines += ["*{}*".format(e["citation"]), ""]
        tag = "note" if e["kind"] == "note" else (e["meta"] or "annotation")
        lines.append("### {}".format(e["title"]))
        lines.append("`{} · {} UTC`".format(tag, e["stamp"]))
        lines.append("")
        lines.append(e["body"])
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="Aggregate Zotero notes and annotations for one day.")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--data-dir", default="~/Zotero")
    ap.add_argument("--out-dir", default="~/zotero-daily")
    ap.add_argument("--by", choices=["modified", "added"], default="modified")
    ap.add_argument("--utc", action="store_true", help="Use UTC day instead of local.")
    ap.add_argument("--notes-only", action="store_true", help="Skip PDF annotations.")
    args = ap.parse_args()

    db_path = find_zotero_db(args.data_dir)
    start, end = day_window_utc(args.date, args.utc)

    conn, tmp_path = open_safe_copy(db_path)
    try:
        entries = fetch_entries(conn, start, end, args.by, not args.notes_only)
    finally:
        conn.close()
        cleanup(tmp_path)

    md = render_markdown(entries, args.date, args.by)
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "{}.md".format(args.date)
    out_file.write_text(md, encoding="utf-8")
    n_notes = sum(1 for e in entries if e["kind"] == "note")
    n_annot = sum(1 for e in entries if e["kind"] == "annotation")
    print("Wrote {} note(s) + {} annotation(s) to {}".format(n_notes, n_annot, out_file))


if __name__ == "__main__":
    main()
