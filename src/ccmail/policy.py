"""Decides whether an incoming hook event produces an email.

Design goal: as few emails as possible. A mail is sent only if every gate passes.

Kinds of Stop mail (see handle_event):
  bg_done    background tasks went from "some" to "none" -> one "finished" mail
  bg_status  periodic check while tasks are still running (heartbeat) -> status mail
  (none)     ordinary end of a turn: only if the turn lasted >= min_elapsed_s
A Stop with tasks still running and no heartbeat due is intermediate and stays silent.
"""
import json
import time

from . import chats, projroot, render, suggest
from .redact import redact

MAIL_EVENTS = ("Stop", "Notification", "StopFailure", "SessionEnd")
# Events that prove Claude is working or the session changed. `Notification` is deliberately NOT here: Claude
# Code sends `idle_prompt` about a minute after a chat stops, and counting it as activity cancelled the parked
# wake hook, so the chat silently stopped listening for e-mail replies.
ACTIVITY_EVENTS = ("UserPromptSubmit", "PostToolUse", "PostToolBatch", "SessionStart", "SessionEnd")
STALE_AFTER_S = 900  # spooled events older than this are recorded but never mailed
HEARTBEAT_SLACK = 0.8


def _summary(event, payload):
    text = payload.get("last_assistant_message") or payload.get("message") or payload.get("error") or ""
    return render.clean_line(redact(str(text)), 200)


def _task_ids(payload):
    return [str(t.get("id")) for t in (payload.get("background_tasks") or []) if isinstance(t, dict)]


def handle_event(db, payload: dict, now=None, received_at=None) -> str:
    """Process one hook payload. Returns the decision string (also stored in the events table)."""
    now = now or time.time()
    s = db.get_settings()
    event = str(payload.get("hook_event_name") or "")
    sid = str(payload.get("session_id") or "?")[:80]
    ntype = str(payload.get("notification_type") or "")
    sess = db.touch_session(sid, str(payload.get("cwd") or ""), now,
                            project_dir=str(payload.get("project_dir") or "") or None,
                            transcript_path=str(payload.get("transcript_path") or "") or None)
    sess = chats.link(db, sess, now)  # adopts the desktop chat's name and project when known
    sess = projroot.resolve(db, sess, str(payload.get("project_dir") or "") or None, now)
    if event != "PostToolBatch" and not sess.get("chat_id") and sess.get("transcript_path"):
        t = chats.transcript_title(sess["transcript_path"])  # name of a chat outside the desktop app
        if t and t != sess.get("title"):
            db.set_session(sid, title=t)
            sess = {**sess, "title": t}
    chat_title = db.chat_title(sess)

    def done(decision, mail=None, summary=None):
        eid = db.add_event(now, sid, event, ntype, summary if summary is not None else _summary(event, payload),
                           decision)
        if mail:
            db.enqueue(eid, mail[0], mail[1], now)
        return decision

    if event == "UserPromptSubmit":
        # heartbeat_pending is deliberately kept: the wake-up turn may itself surface as a prompt event
        db.set_session(sid, turn_start_at=now, state="active")
        return done("recorded")
    if event == "SessionStart":
        db.set_session(sid, turn_start_at=None, state="active", bg_tasks=0, bg_ids="[]", heartbeat_pending=0)
        return done("recorded")
    if event == "PostToolBatch":  # liveness only: Claude finished a round of tool calls and keeps going
        db.set_session(sid, state="active")
        return "ignored"
    if event == "PostToolUse":  # only used to notice that Claude is working / started a background task
        db.set_session(sid, state="active")
        resp = payload.get("tool_response") if isinstance(payload.get("tool_response"), dict) else {}
        if (payload.get("tool_input") or {}).get("run_in_background") and resp.get("backgroundTaskId"):
            return done("recorded", summary="task in background avviato: " + render.clean_line(
                str(resp["backgroundTaskId"]), 40))
        return "ignored"
    if event not in MAIL_EVENTS:
        return done("ignored")

    elapsed = (now - sess["turn_start_at"]) if sess.get("turn_start_at") else None
    kind = None
    reply_turn = False
    limit_hit = (event in ("Stop", "StopFailure")
                 and suggest.is_usage_limit(str(payload.get("last_assistant_message") or ""),
                                            str(payload.get("error") or "")))
    if event == "Stop":
        now_ids = _task_ids(payload)
        prev_ids = json.loads(sess.get("bg_ids") or "[]")
        pending = bool(sess.get("heartbeat_pending"))
        reply_turn = bool(sess.get("reply_pending"))
        db.set_session(sid, turn_start_at=None, state="idle", bg_tasks=len(now_ids),
                       bg_ids=json.dumps(now_ids), heartbeat_pending=0, reply_pending=0)
        if limit_hit:
            kind = None  # a usage-limit notice outranks every other kind of Stop mail
        elif prev_ids and not now_ids:
            kind = "bg_done"
        elif reply_turn:
            kind = None  # the answer to an e-mail reply is mailed even if processes are still running
        elif now_ids:
            interval = s["heartbeat_minutes"] * 60
            last = sess.get("last_sent_at")
            self_scheduled = (s["heartbeat_mode"] == "skill" and elapsed is None
                              and (last is None or now - last >= HEARTBEAT_SLACK * interval))
            if pending or self_scheduled:
                kind = "bg_status"
            elif not s["notify_stop_with_bg"]:
                return done("skip:bg_running")
    elif event == "SessionEnd":
        db.set_session(sid, turn_start_at=None, state="ended")

    reason = _gate(db, s, event, payload, ntype, sess, elapsed, now, received_at, kind, limit_hit,
                   reply_turn=reply_turn)
    if reason:
        return done("skip:" + reason)
    ref = db.new_mail_ref(sess.get("project_dir"), now, sid) if s.get("inbound_enabled") else None
    subject, body = render.build(event, payload, s, sess.get("project") or "", elapsed, kind=kind,
                                 n_bg=len(_task_ids(payload)), chat_title=chat_title, limit_hit=limit_hit,
                                 ref=ref, inbound=bool(ref), to_claude=bool(s.get("inbound_to_claude")),
                                 reply_turn=reply_turn)
    db.set_session(sid, last_sent_at=now)
    tag = "limit" if limit_hit else ("reply" if reply_turn and kind is None else kind)
    return done("queued" if tag is None else "queued:" + tag, (subject, body))


def simulate(db, event, elapsed_s=None, message="", ntype="", session_id=None, now=None,
             bg_running=0, kind=None) -> dict:
    """Dry run with the current settings: what would happen for such an event? Writes nothing."""
    now = now or time.time()
    s = db.get_settings()
    sess = (db.one("SELECT * FROM sessions WHERE session_id=?", (session_id,)) if session_id else None) or {}
    payload = {"hook_event_name": event, "session_id": session_id or "sim", "notification_type": ntype,
               "last_assistant_message": message, "message": message,
               "background_tasks": [{"id": f"t{i}"} for i in range(bg_running)]}
    limit_hit = (event in ("Stop", "StopFailure") and suggest.is_usage_limit(message or "", ""))
    if event == "Stop" and kind is None and bg_running and not s["notify_stop_with_bg"] and not limit_hit:
        reason = "bg_running"
    else:
        reason = _gate(db, s, event, payload, ntype, sess, elapsed_s, now, None, kind, limit_hit)
    if reason is None and not db.project_enabled(sess.get("project_dir")):
        reason = "project_off"
    inbound = bool(s.get("inbound_enabled"))
    subject, body = render.build(event, payload, s, sess.get("project") or "progetto", elapsed_s,
                                 kind=kind, n_bg=bg_running, chat_title=db.chat_title(sess), limit_hit=limit_hit,
                                 ref="ccm-000000000000" if inbound else None, inbound=inbound,
                                 to_claude=bool(s.get("inbound_to_claude")))
    return {"would_send": reason is None, "reason": reason, "subject": subject, "body": body}


def _gate(db, s, event, payload, ntype, sess, elapsed, now, received_at, kind=None, limit_hit=False,
          reply_turn=False):
    if not s["enabled"]:
        return "disabled"
    if now < (s.get("snooze_until") or 0):
        return "snoozed"
    if not db.project_enabled(sess.get("project_dir")):
        return "project_off"  # per-project switch in the web app; checked early so it is never masked
    if received_at is not None and now - received_at > STALE_AFTER_S:
        return "stale"
    if not s["events_enabled"].get("UsageLimit" if limit_hit else event, False):
        return "event_off"
    if event == "Notification" and ntype and ntype not in s["notify_types"]:
        return "type_off"
    if event == "Stop" and kind is None and not limit_hit:
        # A reply is handed to the chat through a Stop hook, so the turn it starts ends with stop_hook_active set:
        # that is the expected path for a reply (one-shot flag, no loop), not a hook feedback loop.
        if payload.get("stop_hook_active") and not reply_turn:
            return "stop_hook_loop"
        if not reply_turn and (elapsed is None or elapsed < s["min_elapsed_s"]):  # unknown duration = short
            return "short_turn"
    if now - (s.get("toggled_at") or 0) < s["mute_after_toggle_s"]:
        return "muted_after_toggle"
    last = sess.get("last_sent_at")
    if kind is None and not limit_hit and not reply_turn and last and now - last < s["cooldown_s"]:
        return "cooldown"
    if db.sent_last_hour(now) >= s["max_per_hour"]:
        return "hourly_cap"
    return None
