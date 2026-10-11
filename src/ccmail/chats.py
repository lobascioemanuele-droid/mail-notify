"""Read-only link to the Claude desktop app's chat metadata (chat title, project, archived flag).

The app keeps one small JSON per chat under <profile>/claude-code-sessions/<account>/<org>/local_*.json.
It is an undocumented, app-internal format: everything here is best effort and fails soft. Only a handful
of fields are read (ids, title, cwd, archived, last activity); nothing else is stored or logged.
`cliSessionId` is the same id that Claude Code hooks send as `session_id`, which is how a hook event is
tied to a chat name.

Where the profile lives matters on Windows: the app is an MSIX package and Python from the Microsoft
Store is one too, so for such a Python `%APPDATA%\\Claude` simply does not exist. Every plausible root is
therefore probed and the results are merged (see `roots()` and `diagnostics()`).
"""
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from .db import leaf_name, norm_dir

SCAN_MIN_INTERVAL_S = 10
MAX_FILE_BYTES = 2_000_000
TAIL_BYTES = 256 * 1024
_lock = threading.Lock()
_mtimes = {}
_last_scan = 0.0
_title_cache = {}  # transcript path -> (mtime_ns, size, title)


# --------------------------------------------------------------------------- locations
def _windows() -> bool:
    return os.name == "nt"


def roots():
    """Candidate claude-code-sessions folders, most specific first (env override, packaged app, classic)."""
    env = os.environ.get("CCMAIL_CLAUDE_SESSIONS")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p]
    out = []
    if _windows():
        local = os.environ.get("LOCALAPPDATA")
        if local:
            pk = Path(local, "Packages")
            if pk.is_dir():
                out += sorted(pk.glob("Claude_*/LocalCache/Roaming/Claude/claude-code-sessions"))
        appdata = os.environ.get("APPDATA")
        if appdata:
            out.append(Path(appdata, "Claude", "claude-code-sessions"))
    else:
        home = Path.home()
        out += [home / "Library" / "Application Support" / "Claude" / "claude-code-sessions",
                home / ".config" / "Claude" / "claude-code-sessions"]
    seen, uniq = set(), []
    for p in out:
        if str(p).lower() not in seen:
            seen.add(str(p).lower())
            uniq.append(p)
    return uniq


def existing_roots():
    return [r for r in roots() if r.is_dir()]


def available() -> bool:
    return bool(existing_roots())


def reset_cache() -> None:
    global _last_scan
    with _lock:
        _mtimes.clear()
        _title_cache.clear()
        _last_scan = 0.0


# --------------------------------------------------------------------------- reading
def _read(path: Path):
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not d.get("sessionId"):
        return None
    root = str(d.get("originCwd") or d.get("cwd") or "")
    last = d.get("lastActivityAt")
    return {
        "chat_id": str(d["sessionId"])[:80],
        "cli": str(d.get("cliSessionId") or "")[:80] or None,
        "title": " ".join(str(d.get("title") or "").split())[:200],
        "root": root,
        "archived": 1 if d.get("isArchived") else 0,
        "last": (last / 1000.0) if isinstance(last, (int, float)) and last > 0 else None,
    }


def read_all():
    """Parse every chat file in every root without touching the database. Yields (path, parsed)."""
    for root in existing_roots():
        for f in root.glob("*/*/local_*.json"):
            c = _read(f)
            if c:
                yield f, c


def scan(db, now=None, force=False) -> int:
    """Refresh the chats table from changed metadata files. Returns the number of files processed."""
    global _last_scan
    now = now or time.time()
    rs = existing_roots()
    if not rs:
        return 0
    with _lock:
        if not force and now - _last_scan < SCAN_MIN_INTERVAL_S:
            return 0
        _last_scan = now
        n = 0
        for root in rs:
            for f in root.glob("*/*/local_*.json"):
                try:
                    mt = f.stat().st_mtime_ns
                except OSError:
                    continue
                if not force and _mtimes.get(str(f)) == mt:
                    continue
                _mtimes[str(f)] = mt
                c = _read(f)
                if not c:
                    continue
                n += 1
                key = norm_dir(c["root"])
                db.x("INSERT INTO chats(chat_id, cli_session_id, title, cwd, project_dir, archived, last_activity, "
                     "updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET "
                     "cli_session_id=excluded.cli_session_id, title=excluded.title, cwd=excluded.cwd, "
                     "project_dir=excluded.project_dir, archived=excluded.archived, "
                     "last_activity=MAX(COALESCE(chats.last_activity,0), COALESCE(excluded.last_activity,0)), "
                     "updated_at=excluded.updated_at",
                     (c["chat_id"], c["cli"], c["title"], c["root"], key, c["archived"], c["last"], now))
                if c["cli"]:  # the hook-side session id of this chat: link it and adopt the chat's project
                    db.x("UPDATE sessions SET chat_id=?, project_dir=?, project=? WHERE session_id=?",
                         (c["chat_id"], key, leaf_name(c["root"]), c["cli"]))
                if key:
                    db.x("INSERT INTO projects(project_dir, name, first_seen, last_seen) VALUES(?,?,?,?) "
                         "ON CONFLICT(project_dir) DO UPDATE SET name=excluded.name, "
                         "last_seen=MAX(COALESCE(last_seen,0), excluded.last_seen)",
                         (key, leaf_name(c["root"]), now, c["last"] or now))
        return n


def _adopt(db, sid: str) -> bool:
    """Link session `sid` to a chat already in the table (the chat file may have been read before the
    session first reported). Returns True if linked."""
    c = db.one("SELECT chat_id, project_dir, cwd FROM chats WHERE cli_session_id=? LIMIT 1", (sid,))
    if not c:
        return False
    db.x("UPDATE sessions SET chat_id=?, project_dir=?, project=? WHERE session_id=?",
         (c["chat_id"], c["project_dir"], leaf_name(c["cwd"]), sid))
    return True


def link(db, session_row: dict, now=None) -> dict:
    """Make sure `session_row` knows its chat (scanning if it is new). Returns the possibly refreshed row."""
    if session_row.get("chat_id"):
        return session_row
    sid = session_row["session_id"]
    if not _adopt(db, sid):
        scan(db, now)
        _adopt(db, sid)
    fresh = db.one("SELECT * FROM sessions WHERE session_id=?", (sid,))
    return fresh or session_row


# --------------------------------------------------------------------------- transcript titles
_TITLE_RE = re.compile(rb'"type"\s*:\s*"custom-title"[^\n]*?"customTitle"\s*:\s*"((?:[^"\\]|\\.)*)"')
_TITLE_RE2 = re.compile(rb'"customTitle"\s*:\s*"((?:[^"\\]|\\.)*)"[^\n]*?"type"\s*:\s*"custom-title"')


def transcript_title(path) -> str:
    """Latest `custom-title` of a transcript, read from the tail of the file only (cached by mtime)."""
    if not path:
        return ""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return ""
    hit = _title_cache.get(str(p))
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    title = ""
    try:
        with p.open("rb") as fh:
            fh.seek(max(0, st.st_size - TAIL_BYTES))
            tail = fh.read()
        for line in reversed(tail.split(b"\n")):
            if b"custom-title" not in line:
                continue
            try:
                o = json.loads(line)
                if o.get("type") == "custom-title" and o.get("customTitle"):
                    title = " ".join(str(o["customTitle"]).split())[:200]
                    break
            except ValueError:
                m = _TITLE_RE.search(line) or _TITLE_RE2.search(line)
                if m:
                    try:
                        title = " ".join(json.loads(b'"' + m.group(1) + b'"').split())[:200]
                        break
                    except ValueError:
                        continue
    except OSError:
        return ""
    _title_cache[str(p)] = (st.st_mtime_ns, st.st_size, title)
    return title


# --------------------------------------------------------------------------- diagnostics
def environment() -> dict:
    exe = sys.executable.replace("\\", "/").lower()
    return {"python": sys.executable, "store_python": "windowsapps" in exe or "packages/pythonsoftwarefoundation" in exe}


def root_report() -> list:
    out = []
    for r in roots():
        n = 0
        if r.is_dir():
            try:
                n = sum(1 for _ in r.glob("*/*/local_*.json"))
            except OSError:
                n = 0
        out.append({"path": str(r), "exists": r.is_dir(), "chat_files": n})
    return out
