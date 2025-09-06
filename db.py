# db.py
import sqlite3

DB_PATH = "events.sqlite"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Internal/app events
    cur.execute("""
    CREATE TABLE IF NOT EXISTS webhook_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        user_id TEXT,
        ip TEXT,
        user_agent TEXT,
        payload TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    """)

    # Public/visitor events (no country column)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS public_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page TEXT,
        ref TEXT,
        ip TEXT,
        user_agent TEXT,
        payload TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    """)

    # Unique index for UPSERT by (page, ip)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_public_events_page_ip ON public_events(page, ip)")
    conn.commit()

    conn.close()
