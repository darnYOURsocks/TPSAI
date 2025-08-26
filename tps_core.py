#!/usr/bin/env python3
"""
TPS Core - Database and text processing functionality extracted from CLI version
Provides all the core functionality for the web interface
"""

import sqlite3
import json
import os
import re
import datetime
import pathlib
from typing import Tuple, List, Optional

# -----------------------
# Configuration
# -----------------------
DEFAULT_DIR = pathlib.Path(os.environ.get("TPS_DB_DIR", "./data")).resolve()
DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = os.environ.get("TPS_DB", str(DEFAULT_DIR / "tps_app.db"))

# -----------------------
# Utilities
# -----------------------
def now_iso() -> str:
    """Return current UTC timestamp in ISO format"""
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

# -----------------------
# Database Schema
# -----------------------
DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS raw_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'  -- pending | processed | error
);

CREATE TABLE IF NOT EXISTS cleaned_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_id INTEGER NOT NULL UNIQUE,
  clean_text TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (raw_id) REFERENCES raw_entries(id) ON DELETE CASCADE
);
"""

def maybe_create_fts5(conn: sqlite3.Connection) -> bool:
    """Create FTS5 index & triggers if available; return True if enabled."""
    try:
        conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS cleaned_entries_fts
        USING fts5(clean_text, content='cleaned_entries', content_rowid='id');
        """)
        conn.execute("""CREATE TRIGGER IF NOT EXISTS cleaned_ai AFTER INSERT ON cleaned_entries
                        BEGIN INSERT INTO cleaned_entries_fts(rowid, clean_text)
                        VALUES (new.id, new.clean_text); END;""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS cleaned_ad AFTER DELETE ON cleaned_entries
                        BEGIN INSERT INTO cleaned_entries_fts(cleaned_entries_fts, rowid, clean_text)
                        VALUES ('delete', old.id, old.clean_text); END;""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS cleaned_au AFTER UPDATE ON cleaned_entries
                        BEGIN
                          INSERT INTO cleaned_entries_fts(cleaned_entries_fts, rowid, clean_text)
                          VALUES('delete', old.id, old.clean_text);
                          INSERT INTO cleaned_entries_fts(rowid, clean_text)
                          VALUES (new.id, new.clean_text);
                        END;""")
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False

def get_conn() -> sqlite3.Connection:
    """Get database connection with schema initialized"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    maybe_create_fts5(conn)
    return conn

def check_fts_available(conn: sqlite3.Connection) -> bool:
    """Check if FTS5 is available and working"""
    try:
        conn.execute("SELECT count(*) FROM cleaned_entries_fts")
        return True
    except sqlite3.OperationalError:
        return False

# -----------------------
# Text Processing (AI Cleaning Stub)
# -----------------------
def simple_normalize(text: str) -> str:
    """Normalize whitespace and quotes"""
    t = text.replace("\t", " ")
    t = re.sub(r"[ \u00A0]+", " ", t)
    t = re.sub(r"\r\n?", "\n", t)
    t = t.strip()
    t = t.replace(""", '"').replace(""", '"').replace("'", "'")
    return t

def infer_language_guess(text: str) -> str:
    """Simple language detection based on character patterns"""
    if re.search(r"[А-Яа-яЁё]", text): return "Russian"
    if re.search(r"[ぁ-ゟ゠-ヿ一-鿿]", text): return "Japanese/Chinese"
    if re.search(r"[áéíóúñü¿¡]", text): return "Spanish"
    if re.search(r"[àâäéèêëîïôöùûüÿç]", text): return "French"
    if re.search(r"[äöüß]", text): return "German"
    return "English"

def extract_tags(text: str) -> List[str]:
    """Extract hashtags and significant words as tags"""
    tags = re.findall(r"#(\w+)", text)
    tokens = re.findall(r"\b[a-zA-Z]{5,}\b", text.lower())
    for tok in tokens[:5]:
        if tok not in tags:
            tags.append(tok)
    return list(dict.fromkeys(tags))[:10]

def tps_ai_clean(raw_text: str) -> Tuple[str, dict]:
    """
    Main text cleaning function - replace this with actual AI processing
    Returns (cleaned_text, metadata_dict)
    """
    cleaned = simple_normalize(raw_text)
    
    # Count words and characters
    word_count = len(re.findall(r"\w+", cleaned))
    char_count = len(cleaned)
    
    # Detect language
    language = infer_language_guess(cleaned)
    
    # Extract tags
    tags = extract_tags(cleaned)
    
    # Calculate reading time (assuming 200 words per minute)
    reading_time = max(1, round(word_count / 200))
    
    meta = {
        "source": "tps_ai_processor",
        "word_count": word_count,
        "char_count": char_count,
        "language_guess": language,
        "tags": tags,
        "reading_time_minutes": reading_time,
        "processed_at": now_iso()
    }
    
    return cleaned, meta

# -----------------------
# Database Operations
# -----------------------
def add_raw(conn: sqlite3.Connection, text: str) -> int:
    """Add raw text entry to database"""
    cur = conn.execute(
        "INSERT INTO raw_entries(text, created_at, status) VALUES (?, ?, 'pending')",
        (text, now_iso()),
    )
    conn.commit()
    return cur.lastrowid

def process_pending(conn: sqlite3.Connection) -> Tuple[int, int]:
    """Process all pending raw entries through AI cleaning"""
    rows = conn.execute("SELECT * FROM raw_entries WHERE status='pending' ORDER BY id").fetchall()
    ok = err = 0
    
    for r in rows:
        try:
            cleaned, meta = tps_ai_clean(r["text"])
            conn.execute(
                "INSERT INTO cleaned_entries(raw_id, clean_text, metadata_json, created_at) VALUES (?, ?, ?, ?)",
                (r["id"], cleaned, json.dumps(meta, ensure_ascii=False), now_iso()),
            )
            conn.execute("UPDATE raw_entries SET status='processed' WHERE id=?", (r["id"],))
            ok += 1
        except Exception as e:
            print(f"Error processing entry {r['id']}: {e}")
            conn.execute("UPDATE raw_entries SET status='error' WHERE id=?", (r["id"],))
            err += 1
    
    conn.commit()
    return ok, err

def recent(conn: sqlite3.Connection, limit: int = 10, offset: int = 0):
    """Get recent raw entries with processing status"""
    return conn.execute("""
        SELECT r.id, 
               CASE 
                 WHEN length(r.text) > 100 THEN substr(r.text,1,100) || '...'
                 ELSE r.text
               END AS text_preview,
               r.text,
               r.status, 
               r.created_at,
               ce.clean_text IS NOT NULL AS has_clean,
               ce.id as clean_id,
               ce.metadata_json
        FROM raw_entries r
        LEFT JOIN cleaned_entries ce ON ce.raw_id=r.id
        ORDER BY r.id DESC 
        LIMIT ? OFFSET ?""", (limit, offset)).fetchall()

def search_clean(conn: sqlite3.Connection, query: str, use_fts: bool):
    """Search cleaned entries using FTS5 or LIKE"""
    if use_fts:
        return conn.execute("""
            SELECT ce.id, ce.raw_id, 
                   CASE 
                     WHEN length(ce.clean_text) > 200 THEN substr(ce.clean_text,1,200) || '...'
                     ELSE ce.clean_text
                   END AS clean_text_preview,
                   ce.clean_text,
                   ce.metadata_json, 
                   ce.created_at,
                   r.created_at as raw_created_at
            FROM cleaned_entries ce
            JOIN cleaned_entries_fts f ON f.rowid = ce.id
            JOIN raw_entries r ON r.id = ce.raw_id
            WHERE cleaned_entries_fts MATCH ?
            ORDER BY ce.id DESC LIMIT 50
        """, (query,)).fetchall()
    else:
        q = f"%{query}%"
        return conn.execute("""
            SELECT ce.id, ce.raw_id, 
                   CASE 
                     WHEN length(ce.clean_text) > 200 THEN substr(ce.clean_text,1,200) || '...'
                     ELSE ce.clean_text
                   END AS clean_text_preview,
                   ce.clean_text,
                   ce.metadata_json, 
                   ce.created_at,
                   r.created_at as raw_created_at
            FROM cleaned_entries ce
            JOIN raw_entries r ON r.id = ce.raw_id
            WHERE ce.clean_text LIKE ?
            ORDER BY ce.id DESC LIMIT 50
        """, (q,)).fetchall()

def get_entry_detail(conn: sqlite3.Connection, raw_id: int) -> Optional[sqlite3.Row]:
    """Get detailed information for a specific entry"""
    return conn.execute("""
        SELECT r.id, r.text, r.status, r.created_at,
               ce.id as clean_id, ce.clean_text, ce.metadata_json, ce.created_at as clean_created_at
        FROM raw_entries r
        LEFT JOIN cleaned_entries ce ON ce.raw_id = r.id
        WHERE r.id = ?
    """, (raw_id,)).fetchone()

def count_stats(conn: sqlite3.Connection) -> Tuple[int, int, int]:
    """Get entry counts by status"""
    total = conn.execute("SELECT count(*) FROM raw_entries").fetchone()[0]
    processed = conn.execute("SELECT count(*) FROM raw_entries WHERE status='processed'").fetchone()[0]
    errors = conn.execute("SELECT count(*) FROM raw_entries WHERE status='error'").fetchone()[0]
    return total, processed, errors
