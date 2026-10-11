"""SQLite storage. No secrets are ever stored here."""
import json
import sqlite3
import threading
import time

from .config import DEFAULTS

SCHEMA_VERSION = 7
RETENTION_DAYS = 30


def norm_dir(p) -> str:
    """Stable key for a project folder: lowercase, forward slashes, no trailing slash."""
    return str(p or "").replace("\\", "/").rstrip("/").lower()


def leaf_name(p) -> str:
    return str(p or "").replace("\\", "/").rstrip("/").split("/")[-1]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY, cwd TEXT, project TEXT,
  first_seen REAL, last_event_at REAL, turn_start_at REAL, last_sent_at REAL,
  bg_tasks INTEGER DEFAULT 0, state TEXT DEFAULT 'active', watchdog_sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS chats(
  chat_id TEXT PRIMARY KEY, cli_session_id TEXT, title TEXT, cwd TEXT, project_dir TEXT,
  archived INTEGER DEFAULT 0, last_activity REAL, updated_at REAL);
CREATE INDEX IF NOT EXISTS ix_chats_cli ON chats(cli_session_id);
CREATE TABLE IF NOT EXISTS projects(
  project_dir TEXT PRIMARY KEY, name TEXT, enabled INTEGER DEFAULT 1, first_seen REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS mail_refs(
  ref TEXT PRIMARY KEY, project_dir TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS inbound_seen(msg_id TEXT PRIMARY KEY, at REAL);
CREATE TABLE IF NOT EXISTS inbound_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, project_dir TEXT, text TEXT, created_at REAL,
  delivered_at REAL);
CREATE INDEX IF NOT EXISTS ix_inbound_pending ON inbound_messages(session_id, delivered_at);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, received_at REAL, session_id TEXT,
  event TEXT, notification_type TEXT, summary TEXT, decision TEXT);
CREATE INDEX IF NOT EXISTS ix_events_t ON events(received_at);
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, created_at REAL,
  subject TEXT, body TEXT, status TEXT DEFAULT 'queued', attempts INTEGER DEFAULT 0,
  next_try_at REAL, last_error TEXT, sent_at REAL);
CREATE INDEX IF NOT EXISTS ix_outbox_s ON outbox(status, next_try_at);
"""


class DB:
    def __init__(self, path):
        self._c = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._c.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._c.execute("PRAGMA journal_mode=WAL")
            self._c.execute("PRAGMA busy_timeout=5000")
            self._c.executescript(_SCHEMA)
            self._migrate()

    def _migrate(self):
        cols = {r["name"] for r in self._c.execute("PRAGMA table_info(sessions)").fetchall()}
        if "bg_ids" not in cols:  # v1 -> v2: background task tracking and heartbeat state
            self._c.execute("ALTER TABLE sessions ADD COLUMN bg_ids TEXT DEFAULT '[]'")
        if "heartbeat_pending" not in cols:
            self._c.execute("ALTER TABLE sessions ADD COLUMN heartbeat_pending INTEGER DEFAULT 0")
        if "reply_pending" not in cols:  # a turn started by an e-mail reply: its answer is always mailed back
            self._c.execute("ALTER TABLE sessions ADD COLUMN reply_pending INTEGER DEFAULT 0")
        if "chat_id" not in cols:  # v2 -> v3: link to the desktop app's chat, and the project root
            self._c.execute("ALTER TABLE sessions ADD COLUMN chat_id TEXT")
        if "project_dir" not in cols:
            self._c.execute("ALTER TABLE sessions ADD COLUMN project_dir TEXT")
        for col in ("transcript_path", "tdir", "title"):  # v3 -> v4: project resolution + title fallback
            if col not in cols:
                self._c.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")
        rcols = {r["name"] for r in self._c.execute("PRAGMA table_info(mail_refs)").fetchall()}
        if "session_id" not in rcols:  # v6 -> v7: a reply can be routed back to the session that sent the mail
            self._c.execute("ALTER TABLE mail_refs ADD COLUMN session_id TEXT")
        ocols = {r["name"] for r in self._c.execute("PRAGMA table_info(outbox)").fetchall()}
        if "kind" not in ocols:  # v4 -> v5: 'test' rows are recorded but never count against the hourly cap
            self._c.execute("ALTER TABLE outbox ADD COLUMN kind TEXT")
        self._c.execute("INSERT INTO meta VALUES('schema_version', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA_VERSION),))

    def close(self):
        with self._lock:
            self._c.close()

    def q(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._c.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        r = self.q(sql, params)
        return r[0] if r else None

    def x(self, sql, params=()):
        with self._lock:
            return self._c.execute(sql, params)

    # --- settings ---
    def get_settings(self) -> dict:
        s = {k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in DEFAULTS.items()}
        for r in self.q("SELECT key, value FROM settings"):
            if r["key"] in s:
                try:
                    v = json.loads(r["value"])
                except ValueError:
                    continue
                if isinstance(s[r["key"]], dict) and isinstance(v, dict):
                    s[r["key"]] = {**s[r["key"]], **v}  # keys added in later versions keep their default
                else:
                    s[r["key"]] = v
        return s

    def set_settings(self, patch: dict):
        with self._lock:
            for k, v in patch.items():
                self._c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (k, json.dumps(v)))

    # --- sessions ---
    def touch_session(self, sid, cwd, now, project_dir=None, transcript_path=None) -> dict:
        """Upsert a session. The project is only seeded here (cwd / CLAUDE_PROJECT_DIR); the authoritative
        project is decided by projroot.resolve(), which knows about chats and transcript folders."""
        root = project_dir or cwd or ""
        tdir = None
        if transcript_path:
            parts = str(transcript_path).replace("\\", "/").split("/")
            if "projects" in parts and parts.index("projects") + 1 < len(parts) - 1:
                tdir = parts[parts.index("projects") + 1]
        with self._lock:
            self._c.execute(
                "INSERT INTO sessions(session_id, cwd, project, project_dir, first_seen, last_event_at, "
                "transcript_path, tdir) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET last_event_at=excluded.last_event_at, "
                "cwd=COALESCE(NULLIF(excluded.cwd,''), cwd), watchdog_sent=0, "
                "transcript_path=COALESCE(NULLIF(excluded.transcript_path,''), transcript_path), "
                "tdir=COALESCE(NULLIF(excluded.tdir,''), tdir)",
                (sid, cwd, leaf_name(root), norm_dir(root), now, now, transcript_path or "", tdir or ""))
            return self.one("SELECT * FROM sessions WHERE session_id=?", (sid,))

    # --- projects / chats ---
    def project_enabled(self, project_dir) -> bool:
        """Per-project switch; projects never seen (or without a row) follow the global switch."""
        r = self.one("SELECT enabled FROM projects WHERE project_dir=?", (norm_dir(project_dir),))
        return True if r is None else bool(r["enabled"])

    def set_project_enabled(self, project_dir, name, enabled: bool, now):
        key = norm_dir(project_dir)
        self.x("INSERT INTO projects(project_dir, name, enabled, first_seen, last_seen) VALUES(?,?,?,?,?) "
               "ON CONFLICT(project_dir) DO UPDATE SET enabled=excluded.enabled",
               (key, name or leaf_name(project_dir), int(enabled), now, now))

    def new_mail_ref(self, project_dir, now, session_id=None) -> str:
        """Opaque token printed in a mail; a reply carrying it may switch that project on/off."""
        import secrets
        ref = "ccm-" + secrets.token_hex(6)
        self.x("INSERT INTO mail_refs(ref, project_dir, created_at, session_id) VALUES(?,?,?,?)",
               (ref, norm_dir(project_dir), now, session_id))
        return ref

    def ref_session(self, ref):
        r = self.one("SELECT session_id FROM mail_refs WHERE ref=?", (ref,))
        return (r or {}).get("session_id")

    def add_inbound_message(self, session_id, project_dir, text, now) -> int:
        cur = self.x("INSERT INTO inbound_messages(session_id, project_dir, text, created_at) VALUES(?,?,?,?)",
                     (session_id, norm_dir(project_dir), text, now))
        return cur.lastrowid

    def pending_inbound(self, session_id, now, max_age_s=24 * 3600):
        return self.q("SELECT id, text, created_at FROM inbound_messages WHERE session_id=? AND delivered_at IS NULL "
                      "AND created_at>? ORDER BY id", (session_id, now - max_age_s))

    def mark_inbound_delivered(self, ids, now):
        for i in ids:
            self.x("UPDATE inbound_messages SET delivered_at=? WHERE id=?", (now, i))

    def lookup_ref(self, ref, now, max_age_s=14 * 86400):
        r = self.one("SELECT project_dir, created_at FROM mail_refs WHERE ref=?", (ref,))
        return r["project_dir"] if r and now - r["created_at"] <= max_age_s else None

    def chat_title(self, session_row) -> str:
        """Name of the chat as in the desktop app; falls back to the title found in the transcript."""
        cid = (session_row or {}).get("chat_id")
        if cid:
            r = self.one("SELECT title FROM chats WHERE chat_id=?", (cid,))
            if (r or {}).get("title"):
                return r["title"]
        return (session_row or {}).get("title") or ""

    def set_session(self, sid, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE sessions SET {cols} WHERE session_id=?", (*fields.values(), sid))

    # --- events / outbox ---
    def add_event(self, now, sid, event, ntype, summary, decision) -> int:
        cur = self.x("INSERT INTO events(received_at, session_id, event, notification_type, summary, decision) "
                     "VALUES(?,?,?,?,?,?)", (now, sid, event, ntype, summary, decision))
        return cur.lastrowid

    def enqueue(self, event_id, subject, body, now) -> int:
        cur = self.x("INSERT INTO outbox(event_id, created_at, subject, body, next_try_at) VALUES(?,?,?,?,?)",
                     (event_id, now, subject, body, now))
        return cur.lastrowid

    def due_outbox(self, now):
        return self.q("SELECT * FROM outbox WHERE status='queued' AND next_try_at<=? ORDER BY id LIMIT 20", (now,))

    def sent_last_hour(self, now) -> int:
        r = self.one("SELECT COUNT(*) n FROM outbox WHERE created_at>? AND status!='failed' "
                     "AND COALESCE(kind,'')!='test'", (now - 3600,))
        return r["n"]

    def outbox_counts(self) -> dict:
        return {r["status"]: r["n"] for r in self.q("SELECT status, COUNT(*) n FROM outbox GROUP BY status")}

    def purge(self, now):
        cut = now - RETENTION_DAYS * 86400
        self.x("DELETE FROM events WHERE received_at<?", (cut,))
        self.x("DELETE FROM outbox WHERE created_at<? AND status!='queued'", (cut,))
        self.x("DELETE FROM sessions WHERE last_event_at<?", (cut,))
        self.x("DELETE FROM mail_refs WHERE created_at<?", (cut,))
        self.x("DELETE FROM inbound_seen WHERE at<?", (cut,))
        self.x("DELETE FROM inbound_messages WHERE created_at<?", (cut,))


def set_enabled(db: DB, enabled: bool, now=None):
    """Single place that flips the switch; also mirrors it to the flag file used by the hook client."""
    from . import paths
    db.set_settings({"enabled": bool(enabled), "toggled_at": now or time.time()})
    flag = paths.enabled_flag()
    if enabled:
        flag.touch()
    else:
        flag.unlink(missing_ok=True)
