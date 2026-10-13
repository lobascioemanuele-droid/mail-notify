"""Loopback-only HTTP API + static web app."""
import json
import logging
import re
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from . import __version__, activity, chats, config, credstore, diagnostics, i18n, installer, mailer, policy
from .db import norm_dir, set_enabled
from .heartbeat import Heartbeat, reply_message, wake_message

LISTEN_PERIOD_S = 3 * 3600  # how long an idle session waits for an e-mail reply (hook timeout is 15000 s)

log = logging.getLogger("ccmail.server")
MAX_BODY = 256 * 1024
COOKIE = "ccmail_s"
_CTYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
           ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml"}
_STATIC_NAME = re.compile(r"^/([A-Za-z0-9_-]+\.(?:html|css|js|svg))$")


def _static(path):
    """(filename, content-type) for files shipped in ccmail/web, else None. Flat names only: no traversal."""
    name = "index.html" if path == "/" else (_STATIC_NAME.match(path) or [None, None])[1]
    if not name:
        return None
    f = resources.files("ccmail").joinpath("web", name)
    return (name, _CTYPES[name[name.rfind("."):]]) if f.is_file() else None


def take_pending_replies(db, sid, via):
    """Pending e-mail replies of session `sid` as ONE reminder text, marked delivered (None if nothing).

    via: "wake" (hook woke an idle chat) | "PostToolBatch" | "UserPromptSubmit" (in-turn delivery)."""
    now = time.time()
    pend = db.pending_inbound(sid, now)
    if not pend:
        return None
    from .inbound import reply_body  # idempotent: also cleans messages queued by an older version
    texts, seen = [], set()
    for p in pend:  # the same sentence sent twice (or a quoted repeat) is delivered once
        t = reply_body(p["text"])
        key = " ".join(t.lower().split()).rstrip(".!")
        if t and key not in seen:
            seen.add(key)
            texts.append(t)
    db.mark_inbound_delivered([p["id"] for p in pend], now)
    db.add_event(now, sid, "InboundDelivered", "", f"{len(pend)} messaggio/i consegnato/i a Claude ({via})",
                 "ok:delivered")
    # Claude is about to work on it; the user is away, so the end of this turn is always mailed back
    db.set_session(sid, state="active", reply_pending=1)
    return reply_message(texts, i18n.resolve(db.get_settings())) if texts else None


class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # a second daemon must fail to bind (single instance)
    daemon_threads = True

    def __init__(self, addr, db, auth, worker, on_shutdown):
        self.db, self.auth, self.worker, self.on_shutdown = db, auth, worker, on_shutdown
        self.heartbeat = Heartbeat()
        super().__init__(addr, Handler)

    def server_close(self):
        self.heartbeat.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = "ccmail"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # never log request lines (may contain nonces)
        pass

    # ---- plumbing ----
    def _send(self, code, body: bytes, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                         "img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj).encode(), extra=extra)

    def _err(self, code, msg):
        self._json(code, {"error": msg})

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        if n and not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise ValueError("content-type must be application/json")
        return json.loads(self._raw or b"{}")

    def _drain(self):
        """Always consume the request body so a keep-alive connection never desyncs."""
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            self.close_connection = True
            self._raw = b""
            raise ValueError("body too large")
        self._raw = self.rfile.read(n) if n else b""

    def _principal(self, write: bool):
        """'bearer', 'cookie' or None. Cookie writes also need a matching CSRF header."""
        if self.server.auth.check_bearer(self.headers.get("Authorization")):
            return "bearer"
        c = SimpleCookie(self.headers.get("Cookie", ""))
        sid = c[COOKIE].value if COOKIE in c else None
        csrf = self.server.auth.session_csrf(sid)
        if csrf is None:
            return None
        if write:
            import hmac
            if not hmac.compare_digest((self.headers.get("X-CSRF") or "").encode(), csrf.encode()):
                return None
        return "cookie"

    # ---- dispatch ----
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def _dispatch(self, method):
        try:
            self._raw = b""
            if method != "GET":
                self._drain()
            if not self._host_ok():
                return self._err(403, "bad host/origin")
            url = urlparse(self.path)
            path = url.path
            if method == "GET" and _static(path):
                name, ctype = _static(path)
                data = resources.files("ccmail").joinpath("web", name).read_bytes()
                return self._send(200, data, ctype)
            if method == "GET" and path == "/auth":
                n = (parse_qs(url.query).get("n") or [""])[0]
                r = self.server.auth.redeem_nonce(n)
                if not r:
                    return self._err(403, "invalid or expired link; run 'ccmail open'")
                cookie = f"{COOKIE}={r[0]}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800"
                return self._send(302, b"", extra={"Location": "/", "Set-Cookie": cookie})
            if not path.startswith("/v1/"):
                return self._err(404, "not found")
            write = method != "GET"
            who = self._principal(write)
            if who is None:
                return self._err(401, "unauthorized")
            self._route(method, path, who)
        except (ValueError, json.JSONDecodeError) as e:
            self._err(400, str(e))
        except Exception:
            log.exception("handler error")
            self._err(500, "internal error")

    def _deliver_replies(self, sid):
        """Wake answer carrying the user's pending e-mail replies for this session (marks them delivered)."""
        text = take_pending_replies(self.server.db, sid, "wake")
        return {"wake": True, "message": text, "kind": "reply"} if text else None

    def _heartbeat_wait(self, payload) -> dict:
        """Long poll for the wake hook (runs after every Stop).

        Two reasons to wake Claude: (a) the user's e-mail reply arrived (inbound_to_claude); (b) the heartbeat
        period elapsed with background tasks still running. Everything else answers wake=false at once."""
        db = self.server.db
        sid = str(payload.get("session_id") or "?")[:80]
        tasks = [str(t.get("id")) for t in (payload.get("background_tasks") or []) if isinstance(t, dict)]
        s = db.get_settings()
        if not s["enabled"]:
            return {"wake": False}
        listen = bool(s["inbound_enabled"] and s["inbound_to_claude"])
        beat = s["heartbeat_mode"] == "hook" and bool(tasks)
        if listen:  # a reply that arrived while Claude was busy is delivered the moment it stops
            ready = self._deliver_replies(sid)
            if ready:
                return ready
        if not (listen or beat):
            return {"wake": False}
        period = s["heartbeat_minutes"] * 60 if beat else LISTEN_PERIOD_S
        log.info("wake hook parked: session=%s listen=%s heartbeat=%s period=%ds", sid[:8], listen, beat, period)
        outcome = self.server.heartbeat.wait_for(sid, period, listen=listen)
        log.info("wake hook released: session=%s outcome=%s", sid[:8], outcome)
        if outcome == "message":
            return self._deliver_replies(sid) or {"wake": False}
        if outcome != "elapsed" or not beat:
            return {"wake": False}
        s = db.get_settings()  # settings may have changed while parked
        sess = db.one("SELECT state, project_dir FROM sessions WHERE session_id=?", (sid,))
        if (not s["enabled"] or s["heartbeat_mode"] != "hook" or time.time() < (s.get("snooze_until") or 0)
                or not sess or sess["state"] != "idle"):
            return {"wake": False}
        if not db.project_enabled(sess.get("project_dir")):
            return {"wake": False}
        # Claude is about to work again: show it as working until its next Stop
        db.set_session(sid, heartbeat_pending=1, state="active")
        return {"wake": True, "message": wake_message(s["heartbeat_minutes"], tasks, i18n.resolve(s))}

    def _route(self, method, path, who):
        db, srv = self.server.db, self.server
        if (method, path) == ("POST", "/v1/events"):
            payload = self._body()
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            decision = policy.handle_event(db, payload)
            out = {"decision": decision}
            # in-turn delivery: a reply that arrived while Claude is working rides on the next tool batch (or
            # on the next prompt) instead of waiting for the end of the turn
            ev = payload.get("hook_event_name")
            if ev in ("PostToolBatch", "UserPromptSubmit"):
                st = db.get_settings()
                if st["enabled"] and st["inbound_enabled"] and st["inbound_to_claude"]:
                    text = take_pending_replies(db, str(payload.get("session_id") or "?")[:80], ev)
                    if text:
                        out["context"] = text
            if payload.get("hook_event_name") in policy.ACTIVITY_EVENTS:
                srv.heartbeat.note_activity(str(payload.get("session_id") or "?")[:80])
            srv.worker.wake.set()
            return self._json(200, out)
        if (method, path) == ("POST", "/v1/heartbeat/wait"):
            if who != "bearer":
                return self._err(403, "bearer only")
            return self._json(200, self._heartbeat_wait(self._body()))
        if (method, path) == ("GET", "/v1/csrf") and who == "cookie":
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return self._json(200, {"csrf": srv.auth.session_csrf(c[COOKIE].value)})
        if (method, path) == ("POST", "/v1/open-nonce"):
            if who != "bearer":
                return self._err(403, "bearer only")
            return self._json(200, {"nonce": srv.auth.new_nonce()})
        if (method, path) == ("POST", "/v1/shutdown"):
            if who != "bearer":
                return self._err(403, "bearer only")
            self._json(200, {"ok": True})
            return srv.on_shutdown()
        if (method, path) == ("GET", "/v1/status"):
            return self._json(200, status_dict(db))
        if (method, path) == ("GET", "/v1/settings"):
            s = db.get_settings()
            d = dict(config.DEFAULTS)
            for k in ("toggled_at", "snooze_until", "enabled", "port"):
                s.pop(k, None)
                d.pop(k, None)
            return self._json(200, {"values": s, "defaults": d, "language_resolved": i18n.resolve(s),
                                    "options": {"notification_types": config.NOTIFICATION_TYPES,
                                                "events": config.EVENT_KEYS}})
        if (method, path) == ("GET", "/v1/overview"):
            return self._json(200, overview_dict(db))
        if (method, path) == ("GET", "/v1/sessions"):
            return self._json(200, db.q(
                "SELECT session_id, project, cwd, state, first_seen, last_event_at, turn_start_at, last_sent_at, "
                "bg_tasks, bg_ids, heartbeat_pending FROM sessions ORDER BY last_event_at DESC LIMIT 50"))
        if (method, path) == ("GET", "/v1/projects"):
            qs = parse_qs(urlparse(self.path).query)
            try:
                limit = int(qs.get("chats", [str(activity.DEFAULT_CHAT_LIMIT)])[0])
            except ValueError:
                raise ValueError("chats must be an integer") from None
            if not 0 <= limit <= 200:
                raise ValueError("chats must be between 0 (all) and 200")
            chats.scan(db)
            return self._json(200, activity.projects_tree(db, app_available=chats.available(), chat_limit=limit))
        if (method, path) == ("GET", "/v1/diagnostics"):
            d = diagnostics.collect(db)
            d["parked_sessions"] = [{"session": k[:8], "since": v} for k, v in srv.heartbeat.parked().items()]
            return self._json(200, d)
        if (method, path) == ("PUT", "/v1/projects/enabled"):
            b = self._body()
            key = b.get("project_dir")
            if not isinstance(key, str) or not key or len(key) > 500:
                raise ValueError("project_dir must be a non-empty string")
            if not isinstance(b.get("enabled"), bool):
                raise ValueError("enabled must be boolean")
            key = norm_dir(key)
            known = db.one("SELECT name FROM projects WHERE project_dir=?", (key,)) or \
                db.one("SELECT project_dir FROM chats WHERE project_dir=? LIMIT 1", (key,))
            if not known:
                raise ValueError("unknown project")
            db.set_project_enabled(key, (known or {}).get("name"), b["enabled"], time.time())
            return self._json(200, {"project_dir": key, "enabled": b["enabled"]})
        if (method, path) == ("POST", "/v1/simulate"):
            b = self._body()
            ev = b.get("event")
            if ev not in policy.MAIL_EVENTS:
                raise ValueError("event must be one of " + ", ".join(policy.MAIL_EVENTS))
            el = b.get("elapsed_s")
            if el is not None and (isinstance(el, bool) or not isinstance(el, (int, float)) or el < 0):
                raise ValueError("elapsed_s must be a non-negative number")
            bg = b.get("bg_running", 0)
            if isinstance(bg, bool) or not isinstance(bg, int) or not 0 <= bg <= 50:
                raise ValueError("bg_running must be an integer between 0 and 50")
            kind = b.get("kind")
            if kind not in (None, "bg_done", "bg_status"):
                raise ValueError("kind must be null, bg_done or bg_status")
            return self._json(200, policy.simulate(db, ev, el, str(b.get("message") or "")[:5000],
                                                   str(b.get("notification_type") or "")[:60],
                                                   str(b.get("session_id") or "")[:80] or None,
                                                   bg_running=bg, kind=kind))
        if (method, path) == ("POST", "/v1/snooze"):
            m = self._body().get("minutes")
            if isinstance(m, bool) or not isinstance(m, int) or not 0 <= m <= 1440:
                raise ValueError("minutes must be an integer between 0 and 1440 (0 cancels)")
            until = time.time() + m * 60 if m else 0
            db.set_settings({"snooze_until": until})
            return self._json(200, {"snooze_until": until})
        if (method, path) == ("POST", "/v1/history/clear"):
            db.x("DELETE FROM events")
            db.x("DELETE FROM outbox WHERE status!='queued'")
            return self._json(200, {"ok": True})
        if (method, path) == ("PUT", "/v1/settings"):
            clean = config.validate(self._body())
            merged = {**db.get_settings(), **clean}
            msg = config.check_combo(merged)
            if msg:
                raise ValueError(msg)
            db.set_settings(clean)
            return self._json(200, {"ok": True})
        if (method, path) == ("POST", "/v1/toggle"):
            body = self._body()
            if not isinstance(body.get("enabled"), bool):
                raise ValueError("enabled must be boolean")
            set_enabled(db, body["enabled"])  # no confirmation email, by design
            return self._json(200, {"enabled": body["enabled"]})
        if (method, path) == ("GET", "/v1/events"):
            qs = parse_qs(urlparse(self.path).query)
            where, args = [], []
            if qs.get("event"):
                where.append("event=?")
                args.append(qs["event"][0])
            if qs.get("session_id"):
                where.append("session_id=?")
                args.append(qs["session_id"][0])
            if qs.get("decision"):  # "queued", or a prefix such as "skip"
                where.append("decision LIKE ?")
                args.append(qs["decision"][0].replace("%", "") + "%")
            try:
                limit = min(max(int(qs.get("limit", ["100"])[0]), 1), 500)
            except ValueError:
                raise ValueError("limit must be an integer") from None
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            sql = "SELECT * FROM events" + clause + " ORDER BY id DESC LIMIT ?"  # nosec B608: constants, bound values
            return self._json(200, db.q(sql, (*args, limit)))
        if (method, path) == ("GET", "/v1/outbox"):
            return self._json(200, db.q("SELECT id, created_at, subject, status, attempts, last_error, sent_at "
                                        "FROM outbox ORDER BY id DESC LIMIT 100"))
        m = re.fullmatch(r"/v1/outbox/(\d+)/retry", path)
        if method == "POST" and m:
            db.x("UPDATE outbox SET status='queued', attempts=0, next_try_at=? WHERE id=? AND status='failed'",
                 (time.time(), int(m.group(1))))
            srv.worker.wake.set()
            return self._json(200, {"ok": True})
        if (method, path) == ("POST", "/v1/test-email"):
            try:  # explicit user action: the only mail not subject to the gates
                mailer.send_test(db, db.get_settings())
            except mailer.MailError as e:
                return self._json(200, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True})
        self._err(404, "not found")


def overview_dict(db) -> dict:
    """Everything the dashboard header needs in one call."""
    now = time.time()
    st = status_dict(db)
    s = db.get_settings()
    day = now - 86400
    sent = db.one("SELECT COUNT(*) n FROM outbox WHERE status='sent' AND sent_at>?", (day,))["n"]
    skipped = db.one("SELECT COUNT(*) n FROM events WHERE decision LIKE 'skip:%' AND received_at>?", (day,))["n"]
    last = db.one("SELECT subject, sent_at FROM outbox WHERE status='sent' ORDER BY sent_at DESC LIMIT 1")
    reasons = {r["decision"]: r["n"] for r in db.q(
        "SELECT decision, COUNT(*) n FROM events WHERE decision LIKE 'skip:%' AND received_at>? GROUP BY decision",
        (day,))}
    return {
        "version": st["version"], "enabled": st["enabled"], "hooks_installed": st["hooks_installed"],
        "credentials": {"configured": st["credentials"], "sender": st["sender"], "backend": st["credential_backend"]},
        "recipient": s["recipient"] or st["sender"], "warnings": st["warnings"],
        "snooze_until": s["snooze_until"] if s["snooze_until"] > now else 0,
        "last_24h": {"sent": sent, "skipped": skipped, "skipped_by_reason": reasons,
                     # failures only count while no later mail has gone out (a success clears the worry)
                     "failed": db.one("SELECT COUNT(*) n FROM outbox WHERE status='failed' AND created_at>? "
                                      "AND created_at>COALESCE((SELECT MAX(sent_at) FROM outbox "
                                      "WHERE status='sent'),0)", (day,))["n"],
                     "failed_total": db.outbox_counts().get("failed", 0)},
        "heartbeat": {"mode": s["heartbeat_mode"], "minutes": s["heartbeat_minutes"],
                      "notify_stop_with_bg": s["notify_stop_with_bg"]},
        "mute_until":(s["toggled_at"] + s["mute_after_toggle_s"]) if s["toggled_at"] + s["mute_after_toggle_s"] > now else 0,
        "queued": db.outbox_counts().get("queued", 0),
        "hourly": {"used": db.sent_last_hour(now), "cap": s["max_per_hour"]},
        "last_mail": last,
        "active_sessions": db.one("SELECT COUNT(*) n FROM sessions WHERE state!='ended' AND last_event_at>?",
                                  (now - 6 * 3600,))["n"],
    }


def status_dict(db) -> dict:
    s = db.get_settings()
    cred = None
    try:
        cred = credstore.describe()
    except Exception:
        pass
    warnings = []
    if cred and not credstore.is_secure(cred[1]):
        warnings.append(i18n.pick(s, "Credenziali nel file di riserva: l'archivio sicuro del sistema non è disponibile.",
                                  "Credentials in the fallback file: the system's secure store is not available."))
    if not cred:
        warnings.append(i18n.pick(s, "Credenziali non configurate: esegui 'ccmail init' in un terminale.",
                                  "Credentials not configured: run 'ccmail init' in a terminal."))
    try:
        hooks = installer.is_installed()
    except Exception:
        hooks = False
    if not hooks:
        warnings.append(i18n.pick(s, "Hook non installati in settings.json: esegui 'ccmail install-hooks'.",
                                  "Hooks not installed in settings.json: run 'ccmail install-hooks'."))
    combo = config.check_combo(s)
    if combo:  # legacy value saved before the rule existed: the watchdog is paused until it is fixed
        warnings.append(i18n.pick(s, "Watchdog in pausa: " + combo.split(": ", 1)[1] + ". Correggilo in Regole.",
                                  "Watchdog paused: " + combo.split(": ", 1)[1] + ". Fix it in Rules."))
    return {"version": __version__, "enabled": s["enabled"], "credentials": bool(cred),
            "sender": cred[0] if cred else None, "credential_backend": cred[1] if cred else None,
            "hooks_installed": hooks, "outbox": db.outbox_counts(), "warnings": warnings}
