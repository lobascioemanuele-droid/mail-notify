"""Decides which project a session belongs to, never mistaking a subfolder for a project.

Priority, strongest evidence first:
  1. the desktop chat the session belongs to (its `originCwd`)             -> set by chats.link/scan
  2. a known chat whose folder encodes to the session's transcript folder   -> same project
  3. CLAUDE_PROJECT_DIR sent by the hook client
  4. the shortest `cwd` seen among sessions sharing a transcript folder     -> the project root
  5. the session's own cwd
Claude Code names a transcript folder after the folder the session started in, with every character
that is not a letter or digit replaced by '-' (e.g. `C:\\Users\\Lobas\\oropan - Copia` ->
`C--Users-Lobas-oropan---Copia`), which is what makes 2 and 4 possible.
"""
import re

from .db import leaf_name, norm_dir

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


def encode_dir(path: str) -> str:
    return _NON_ALNUM.sub("-", path or "").lower()


def _display_name(db, key: str, fallback: str) -> str:
    r = db.one("SELECT name FROM projects WHERE project_dir=?", (key,))
    return (r or {}).get("name") or fallback


def resolve(db, sess: dict, env_project_dir: str = None, now=None) -> dict:
    """Update and return the session row with the best project. Idempotent and cheap."""
    if sess.get("chat_id"):  # 1: chats.scan/_adopt already stored the chat's project
        return _register(db, sess, sess.get("project_dir"), sess.get("project"), now)
    tdir = (sess.get("tdir") or "").lower()
    key = name = None
    if tdir:
        for c in db.q("SELECT project_dir, cwd FROM chats"):  # 2
            if encode_dir(c["cwd"]) == tdir or encode_dir(c["project_dir"]) == tdir:
                key, name = c["project_dir"], leaf_name(c["cwd"])
                break
    if not key and env_project_dir:  # 3
        key, name = norm_dir(env_project_dir), leaf_name(env_project_dir)
    if not key and tdir:  # 4
        rows = db.q("SELECT cwd FROM sessions WHERE lower(tdir)=? AND cwd!=''", (tdir,))
        cwds = [r["cwd"] for r in rows]
        if cwds:
            root = min(cwds, key=lambda p: len(norm_dir(p)))
            key, name = norm_dir(root), leaf_name(root)
    if not key:  # 5
        key, name = norm_dir(sess.get("cwd")), leaf_name(sess.get("cwd"))
    if key and (key != sess.get("project_dir") or name != sess.get("project")):
        db.set_session(sess["session_id"], project_dir=key, project=name)
        sess = {**sess, "project_dir": key, "project": name}
    if key and tdir:  # siblings recorded before the root was known (e.g. started in a subfolder)
        db.x("UPDATE sessions SET project_dir=?, project=? WHERE lower(tdir)=? AND chat_id IS NULL "
             "AND (project_dir IS NOT ? OR project IS NOT ?)", (key, name, tdir, key, name))
    return _register(db, sess, key, name, now)


def _register(db, sess, key, name, now):
    if key:
        t = now or sess.get("last_event_at") or 0
        db.x("INSERT INTO projects(project_dir, name, first_seen, last_seen) VALUES(?,?,?,?) "
             "ON CONFLICT(project_dir) DO UPDATE SET last_seen=MAX(COALESCE(last_seen,0), excluded.last_seen)",
             (key, name or leaf_name(key), t, t))
    return sess


def repair(db, now=None) -> dict:
    """Re-resolve every stored session and drop projects nothing refers to. Keeps per-project switches."""
    from . import chats as chats_mod
    snap = {r["session_id"]: (r["project_dir"], r["project"])
            for r in db.q("SELECT session_id, project_dir, project FROM sessions")}
    for s in db.q("SELECT * FROM sessions"):
        if not s.get("chat_id"):
            chats_mod._adopt(db, s["session_id"])
            s = db.one("SELECT * FROM sessions WHERE session_id=?", (s["session_id"],))
        resolve(db, s, now=now)
    resolved = sum(1 for r in db.q("SELECT session_id, project_dir, project FROM sessions")
                   if snap.get(r["session_id"]) != (r["project_dir"], r["project"]))
    removed = db.x(
        "DELETE FROM projects WHERE project_dir NOT IN (SELECT project_dir FROM sessions WHERE project_dir IS NOT NULL) "
        "AND project_dir NOT IN (SELECT project_dir FROM chats WHERE project_dir IS NOT NULL) "
        "AND enabled=1").rowcount
    return {"sessions_realigned": resolved, "projects_removed": removed}
