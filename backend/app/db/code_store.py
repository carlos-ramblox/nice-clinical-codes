import sqlite3
import logging
import threading
from pathlib import Path

from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

# Per-thread connections. The graph's parallel retriever fan-out runs
# QOF and OpenCodelists in separate threadpool workers (LangGraph wraps
# sync nodes via run_in_executor); a single shared sqlite3.Connection
# raises ``InterfaceError: bad parameter or other API misuse`` when
# those threads issue overlapping execute() calls. SQLite connections
# are not thread-safe even with check_same_thread=False — that flag
# only suppresses the safety assertion, it does not make the
# underlying C handle reentrant. One connection per thread is the
# canonical fix.
_local = threading.local()


def _get_db_path() -> str:
    # DATABASE_URL is like "sqlite:///./data/codes.db"
    return DATABASE_URL.replace("sqlite:///", "")


def get_connection() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        db_path = _get_db_path()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _init_tables(conn)
        _local.conn = conn
        logger.info("SQLite connected: %s (thread=%s)", db_path, threading.get_ident())
    return conn


def _init_tables(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            term TEXT NOT NULL,
            vocabulary TEXT NOT NULL,
            source TEXT NOT NULL,
            domain TEXT DEFAULT 'Condition',
            cluster_id TEXT,
            cluster_description TEXT,
            active INTEGER DEFAULT 1,
            UNIQUE(code, vocabulary, source)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_codes_cluster ON codes(cluster_description)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_codes_term ON codes(term)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_codes_vocabulary ON codes(vocabulary)")
    conn.commit()


def insert_codes(codes: list[dict]) -> int:
    """Insert codes, skip duplicates. Returns count actually inserted."""
    conn = get_connection()
    inserted = 0
    for c in codes:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO codes
               (code, term, vocabulary, source, domain, cluster_id, cluster_description, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                c.get("code", ""),
                c.get("term", ""),
                c.get("vocabulary", "SNOMED CT"),
                c.get("source", ""),
                c.get("domain", "Condition"),
                c.get("cluster_id", ""),
                c.get("cluster_description", ""),
                c.get("active", 1),
            ),
        )
        inserted += cursor.rowcount
    conn.commit()
    logger.info("Inserted %d codes into SQLite", inserted)
    return inserted


def search_by_condition(condition: str, vocabulary: str | None = None) -> list[dict]:
    """Search codes by cluster description or term. Uses LIKE for fuzzy matching.

    Results are ordered by ``(vocabulary, code)`` so the row sequence is a
    function of the matched code text, not the SQLite rowid (insertion
    order). Determinism across DB rebuilds matters for any future
    merger-side rank fusion: without an explicit ORDER BY, re-ingesting
    the same source files in a different order would shuffle the
    per-source rank assigned to each retrieved code.
    """
    conn = get_connection()
    query = "SELECT * FROM codes WHERE (cluster_description LIKE ? OR term LIKE ?)"
    params: list = [f"%{condition}%", f"%{condition}%"]

    if vocabulary:
        query += " AND vocabulary = ?"
        params.append(vocabulary)

    query += " AND active = 1 ORDER BY vocabulary, code"

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM codes").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM codes GROUP BY source"
    ).fetchall()
    return {"total": total, "by_source": {r["source"]: r["cnt"] for r in by_source}}
