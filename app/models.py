"""SQLite models and CRUD for arXiv papers."""

import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "arxiv.db")


def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            arxiv_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            authors TEXT NOT NULL,
            abstract TEXT,
            comment TEXT,
            categories TEXT,
            published TEXT,
            pdf_url TEXT,
            abs_url TEXT,
            relevance_tier TEXT,
            relevance_summary TEXT,
            date_added TEXT NOT NULL,
            is_favourite INTEGER DEFAULT 0,
            favourite_note TEXT,
            is_dismissed INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_date_added
        ON papers(date_added)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_is_favourite
        ON papers(is_favourite)
    """)
    # Migrate: add is_dismissed if missing
    cursor = conn.execute("PRAGMA table_info(papers)")
    columns = {row[1] for row in cursor.fetchall()}
    if "is_dismissed" not in columns:
        conn.execute("ALTER TABLE papers ADD COLUMN is_dismissed INTEGER DEFAULT 0")
    if "comment" not in columns:
        conn.execute("ALTER TABLE papers ADD COLUMN comment TEXT")
    if "thumbnail" not in columns:
        conn.execute("ALTER TABLE papers ADD COLUMN thumbnail TEXT")
    if "code_url" not in columns:
        conn.execute("ALTER TABLE papers ADD COLUMN code_url TEXT")
    if "project_url" not in columns:
        conn.execute("ALTER TABLE papers ADD COLUMN project_url TEXT")

    conn.commit()
    conn.close()


def _row_to_dict(row):
    """Convert a sqlite3.Row to a dict, parsing JSON fields."""
    d = dict(row)
    for field in ("authors", "categories"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def insert_papers(papers, date_added):
    """Insert or update a list of papers for a given date.

    Args:
        papers: list of dicts with paper data
        date_added: announcement date string (YYYY-MM-DD)

    Returns:
        number of papers inserted/updated
    """
    conn = get_db()
    count = 0
    for p in papers:
        authors = json.dumps(p.get("authors", []))
        categories = json.dumps(p.get("categories", []))
        conn.execute("""
            INSERT INTO papers
                (arxiv_id, title, authors, abstract, comment, categories, published,
                 pdf_url, abs_url, relevance_tier, relevance_summary, date_added)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(arxiv_id) DO UPDATE SET
                title=excluded.title,
                authors=excluded.authors,
                abstract=excluded.abstract,
                comment=excluded.comment,
                categories=excluded.categories,
                published=excluded.published,
                pdf_url=excluded.pdf_url,
                abs_url=excluded.abs_url,
                relevance_tier=excluded.relevance_tier,
                relevance_summary=excluded.relevance_summary
        """, (
            p["arxiv_id"], p["title"], authors,
            p.get("abstract"), p.get("comment"), categories,
            p.get("published"), p.get("pdf_url"), p.get("abs_url"),
            p.get("relevance_tier"), p.get("relevance_summary"),
            date_added,
        ))
        count += 1
    conn.commit()
    conn.close()
    return count


def get_papers_by_date(date_str):
    """Get all papers for a specific date, ordered by tier then title."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM papers
        WHERE date_added = ?
        ORDER BY
            CASE relevance_tier
                WHEN 'high' THEN 0
                WHEN 'moderate' THEN 1
                ELSE 2
            END,
            title
    """, (date_str,)).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_available_dates():
    """Get all dates that have papers, most recent first."""
    conn = get_db()
    rows = conn.execute("""
        SELECT date_added, COUNT(*) as count
        FROM papers
        GROUP BY date_added
        ORDER BY date_added DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_favourites():
    """Get all favourited papers, most recently added first."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM papers
        WHERE is_favourite = 1
        ORDER BY date_added DESC, title
    """).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def toggle_favourite(arxiv_id):
    """Toggle favourite status. Returns new is_favourite value."""
    conn = get_db()
    row = conn.execute(
        "SELECT is_favourite FROM papers WHERE arxiv_id = ?", (arxiv_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return None
    new_val = 0 if row["is_favourite"] else 1
    conn.execute(
        "UPDATE papers SET is_favourite = ? WHERE arxiv_id = ?",
        (new_val, arxiv_id),
    )
    conn.commit()
    conn.close()
    return new_val


def update_favourite_note(arxiv_id, note):
    """Set the note on a favourited paper."""
    conn = get_db()
    conn.execute(
        "UPDATE papers SET favourite_note = ? WHERE arxiv_id = ?",
        (note, arxiv_id),
    )
    conn.commit()
    conn.close()


def toggle_dismissed(arxiv_id):
    """Toggle dismissed status. Returns new is_dismissed value."""
    conn = get_db()
    row = conn.execute(
        "SELECT is_dismissed FROM papers WHERE arxiv_id = ?", (arxiv_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return None
    new_val = 0 if row["is_dismissed"] else 1
    conn.execute(
        "UPDATE papers SET is_dismissed = ? WHERE arxiv_id = ?",
        (new_val, arxiv_id),
    )
    conn.commit()
    conn.close()
    return new_val


def set_thumbnail(arxiv_id, thumbnail_path):
    """Set the thumbnail path for a paper."""
    conn = get_db()
    conn.execute(
        "UPDATE papers SET thumbnail = ? WHERE arxiv_id = ?",
        (thumbnail_path, arxiv_id),
    )
    conn.commit()
    conn.close()


def get_papers_missing_thumbnails():
    """Get all papers that have no thumbnail yet."""
    conn = get_db()
    rows = conn.execute("""
        SELECT arxiv_id, comment, abstract FROM papers
        WHERE thumbnail IS NULL
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
