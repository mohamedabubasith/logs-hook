# db.py
import os, sqlite3, logging

log = logging.getLogger("db")

def _select_db_path() -> str:
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return env_path
    # Prefer persistent /data if available, else /tmp
    for base in ("/data", "/persistent", "/workspace", "/home/user/app/data", "/tmp"):
        try:
            if os.path.isdir(base) and os.access(base, os.W_OK):
                return os.path.join(base, "events.sqlite")
        except Exception:
            continue
    # Last resort: current dir (may be read-only on Spaces)
    return os.path.abspath("events.sqlite")

DB_PATH = _select_db_path()

def _ensure_dir(path: str):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def get_conn():
    _ensure_dir(DB_PATH)
    log.info("Opening SQLite at DB_PATH=%s", DB_PATH)
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
