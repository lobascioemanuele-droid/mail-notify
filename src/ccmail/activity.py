"""Read model for the Attività view: projects -> chats -> latest session, with an honest state label.

States never claim more than the evidence supports (a stale 'idle' is not "waiting for you").
"""
import json
import time

from .db import leaf_name, norm_dir

WORKING_QUIET_S = 5 * 60       # working but no event for this long: say so
WORKING_STALE_S = 30 * 60      # 'active' without any event for this long: no longer trust it
QUIET_AFTER_S = 6 * 3600       # idle for this long: "inattiva", not "in attesa di te"
APP_AHEAD_S = 120              # the app saw activity this much newer than our last hook event
PSEUDO_CHAT_WINDOW_S = 7 * 86400
PSEUDO_CHAT_LIMIT = 15


def _mins(sec) -> str:
    m = int(sec // 60)
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def display_state(sess, chat, now):
    """-> (code, label, detail). `sess` is the chat's latest session row or None."""
    if chat and chat.get("archived"):
        return "archived", "Archiviata", "La chat è archiviata nell'app."
    if not sess:
        return "none", "Nessun evento ricevuto", "ccmail non ha ancora visto eventi di questa chat."
    last = sess.get("last_event_at") or 0
    age = now - last
    app_last = (chat or {}).get("last_activity") or 0
    st = sess.get("state")
    if st == "ended":
        return "ended", "Terminata", "La sessione è stata chiusa."
    if st == "active":
        if age > WORKING_STALE_S:
            return "stale", "Nessun segnale", f"Risultava al lavoro ma non arriva nulla da {_mins(age)}."
        if age > WORKING_QUIET_S:
            return "working", "Al lavoro", f"Nessun evento da {_mins(age)}: forse sta eseguendo un comando lungo."
        return "working", "Al lavoro", "Claude sta lavorando."
    # idle: Claude finished its turn
    if app_last - last > APP_AHEAD_S:
        return ("unknown", "Stato non certo",
                "L'app ha registrato attività più recente di quella vista dagli hook (hook non ricevuti?).")
    if sess.get("heartbeat_pending"):
        return "checking", "Controllo periodico", "Claude è stato risvegliato per controllare i processi."
    if sess.get("bg_tasks") and age > QUIET_AFTER_S:  # old knowledge: do not claim the tasks still run
        return ("quiet", "Inattiva", f"Ferma da {_mins(age)}; l'ultimo stato noto indicava "
                f"{sess['bg_tasks']} processo/i in background.")
    if sess.get("bg_tasks"):
        n = sess["bg_tasks"]
        return ("bg", "Attende i processi in background",
                ("1 processo in corso" if n == 1 else f"{n} processi in corso")
                + ": Claude non aspetta una tua risposta.")
    if age > QUIET_AFTER_S:
        return "quiet", "Inattiva", f"Ferma da {_mins(age)}."
    return "waiting", "In attesa di te", "Claude ha finito il turno."


def _session_view(s):
    return {
        "session_id": s["session_id"], "short": s["session_id"][:8], "first_seen": s["first_seen"],
        "last_event_at": s["last_event_at"], "last_sent_at": s["last_sent_at"],
        "turn_start_at": s["turn_start_at"], "bg_tasks": s["bg_tasks"],
        "bg_ids": json.loads(s.get("bg_ids") or "[]"),
    }


DEFAULT_CHAT_LIMIT = 5


def projects_tree(db, now=None, app_available=False, chat_limit=DEFAULT_CHAT_LIMIT) -> dict:
    """chat_limit: how many (non-archived) chats to return per project, newest first; 0 = all.
    Archived chats are returned separately in `archived` and never count towards the limit."""
    now = now or time.time()
    projects, order = {}, []

    def proj(key, name):
        if key not in projects:
            row = db.one("SELECT enabled FROM projects WHERE project_dir=?", (key,))
            projects[key] = {"project_dir": key, "name": name or leaf_name(key), "enabled": True if row is None
                             else bool(row["enabled"]), "last_activity": None, "chats": []}
            order.append(key)
        return projects[key]

    for r in db.q("SELECT * FROM projects"):
        p = proj(r["project_dir"], r["name"])
        p["enabled"] = bool(r["enabled"])
    sessions = db.q("SELECT * FROM sessions ORDER BY last_event_at DESC")
    by_chat = {}
    for s in sessions:
        if s.get("chat_id"):
            by_chat.setdefault(s["chat_id"], []).append(s)
    for c in db.q("SELECT * FROM chats"):
        p = proj(c["project_dir"], leaf_name(c["cwd"]))
        ss = by_chat.get(c["chat_id"], [])
        latest = ss[0] if ss else None
        code, label, detail = display_state(latest, c, now)
        act = max([x for x in (c["last_activity"], latest and latest["last_event_at"]) if x] or [0]) or None
        p["chats"].append({"chat_id": c["chat_id"], "title": c["title"] or "(senza titolo)",
                           "archived": bool(c["archived"]), "last_activity": act, "state": code,
                           "state_label": label, "state_detail": detail,
                           "session": _session_view(latest) if latest else None, "sessions_seen": len(ss)})
    # sessions that do not belong to any app chat (plain CLI): one pseudo-chat each, recent ones only
    seen = {}
    for s in sessions:
        if s.get("chat_id") or now - (s["last_event_at"] or 0) > PSEUDO_CHAT_WINDOW_S:
            continue
        key = s.get("project_dir") or norm_dir(s.get("cwd"))
        if not key:
            continue
        p = proj(key, s.get("project"))
        if seen.get(key, 0) >= PSEUDO_CHAT_LIMIT:
            continue
        seen[key] = seen.get(key, 0) + 1
        code, label, detail = display_state(s, None, now)
        p["chats"].append({"chat_id": None, "title": f"Sessione {s['session_id'][:8]} (fuori dall'app)",
                           "archived": False, "last_activity": s["last_event_at"], "state": code,
                           "state_label": label, "state_detail": detail, "session": _session_view(s),
                           "sessions_seen": 1})
    out = []
    for key in order:
        p = projects[key]
        everything = sorted(p["chats"], key=lambda c: -(c["last_activity"] or 0))
        live = [c for c in everything if not c["archived"]]
        archived = [c for c in everything if c["archived"]]
        if not everything:
            continue
        p["last_activity"] = max([c["last_activity"] for c in (live or archived) if c["last_activity"]] or [0]) or None
        p["chats_total"] = len(live)
        p["chats"] = live[:chat_limit] if chat_limit else live
        p["chats_hidden"] = len(live) - len(p["chats"])
        p["archived"] = archived
        out.append(p)
    out.sort(key=lambda p: -(p["last_activity"] or 0))
    return {"projects": out, "app_metadata": app_available, "chat_limit": chat_limit, "generated_at": now}
