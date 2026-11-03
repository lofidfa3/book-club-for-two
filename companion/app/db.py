"""SQLite storage for the book club (readers, notes, replies, milestones, love messages)."""
import os
import sqlite3
from contextlib import contextmanager

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "bookclub.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS readers (
    id            INTEGER PRIMARY KEY,
    kavita_id     INTEGER UNIQUE NOT NULL,
    username      TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    api_key       TEXT NOT NULL,
    love_every    INTEGER NOT NULL DEFAULT 1,   -- show a love note every N page turns (0 = off)
    created       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY,
    series_id  INTEGER NOT NULL,
    position   INTEGER NOT NULL,       -- global page index the note is about
    label      TEXT NOT NULL,          -- section label shown to readers
    author_id  INTEGER NOT NULL REFERENCES readers(id),
    body       TEXT NOT NULL,
    created    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS replies (
    id         INTEGER PRIMARY KEY,
    note_id    INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    author_id  INTEGER NOT NULL REFERENCES readers(id),
    body       TEXT NOT NULL,
    created    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS milestones (
    id         INTEGER PRIMARY KEY,
    series_id  INTEGER NOT NULL,
    position   INTEGER NOT NULL,       -- reach the end of this page index
    label      TEXT NOT NULL,
    due_date   TEXT NOT NULL           -- YYYY-MM-DD
);
CREATE TABLE IF NOT EXISTS positions (          -- exact spot in the book for the club reader
    reader_id  INTEGER NOT NULL REFERENCES readers(id),
    chapter_id INTEGER NOT NULL,
    cfi        TEXT NOT NULL,
    updated    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (reader_id, chapter_id)
);
CREATE TABLE IF NOT EXISTS highlights (          -- private: each reader sees only their own
    id         INTEGER PRIMARY KEY,
    reader_id  INTEGER NOT NULL REFERENCES readers(id),
    series_id  INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    cfi_range  TEXT NOT NULL,
    excerpt    TEXT NOT NULL,         -- short snippet shown in the highlights list
    label      TEXT NOT NULL DEFAULT '',
    color      TEXT NOT NULL DEFAULT 'yellow',
    created    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (reader_id, chapter_id, cfi_range)
);
CREATE TABLE IF NOT EXISTS love_seen (          -- which notes each reader has already seen (no repeats)
    reader_id  INTEGER NOT NULL,
    message_id INTEGER NOT NULL REFERENCES love_messages(id) ON DELETE CASCADE,
    PRIMARY KEY (reader_id, message_id)
);
CREATE TABLE IF NOT EXISTS love_messages (
    id         INTEGER PRIMARY KEY,
    author_id  INTEGER REFERENCES readers(id),   -- NULL = built-in default
    body       TEXT NOT NULL,
    created    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

LOVE_DIR = os.path.join(os.path.dirname(__file__), "love")


def builtin_love() -> list[str]:
    """The built-in little love notes (app/love/*.txt, one per line), de-duplicated, in file order."""
    out = []
    for name in sorted(os.listdir(LOVE_DIR)):
        if name.endswith(".txt"):
            with open(os.path.join(LOVE_DIR, name), encoding="utf-8") as f:
                out += [line.strip() for line in f if line.strip()]
    return list(dict.fromkeys(out))


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    os.makedirs(DATA_DIR, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(readers)")}
        if "is_admin" not in cols:    # added in v2
            c.execute("ALTER TABLE readers ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        have = {r["body"] for r in c.execute("SELECT body FROM love_messages WHERE author_id IS NULL")}
        c.executemany("INSERT INTO love_messages (author_id, body) VALUES (NULL, ?)",
                      [(m,) for m in builtin_love() if m not in have])


def get_setting(key, default=None):
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with conn() as c:
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
