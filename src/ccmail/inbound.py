"""Reply-by-email control channel (off until the user enables it).

Two kinds of reply, both from the user's own mailbox:
  * a COMMAND on the first line: OFF / ON / PAUSA [minuti]  -> per-project switch or global pause;
  * (only if `inbound_to_claude` is on) FREE TEXT          -> delivered into the Claude chat of the session
    that sent the mail, as a message of the user, through the wake hook.

Defence in depth, all must hold before anything changes:
  1. the mail was sent by the configured recipient address (From),
  2. it is vouched for: either Gmail marks it with the Sent label (only mail sent by this very account
     has it) or the server reports dkim=pass / spf=pass in Authentication-Results,
  3. it carries a reference token (`ccm-<12 hex>`) that this daemon printed in a mail, not older than 14 days,
  4. it was not processed before (Message-ID) and is not older than the moment the channel was switched on,
  5. commands are whitelisted; free text is delivered only for genuine replies (In-Reply-To / "Re:"), never for
     ccmail's own notifications, and is capped in length.
"""
import email
import imaplib
import logging
import re
import ssl
import threading
import time
from email import policy as email_policy
from email.utils import parseaddr

from . import credstore, i18n
from .db import leaf_name

log = logging.getLogger("ccmail.inbound")
REF_RE = re.compile(r"\bccm-[0-9a-f]{12}\b")
OFF_WORDS = {"OFF", "SPEGNI", "DISATTIVA", "STOP"}
ON_WORDS = {"ON", "ACCENDI", "ATTIVA", "RIATTIVA"}
PAUSE_WORDS = {"PAUSA", "PAUSE", "SNOOZE", "SLEEP"}
MAX_TEXT = 20_000
MAX_PER_POLL = 20   # messages processed per pass
MAX_SCAN = 200      # newest matching messages looked at (header only) per pass
MAX_REPLY = 2000
_REPLY_SUBJECT = re.compile(r"^\s*(re|r|risp|rif)\s*:", re.I)
_DAYS = r"(lun|mar|mer|gio|ven|sab|dom|mon|tue|wed|thu|fri|sat|sun)[a-zì]*"
_QUOTE_INTRO = re.compile(
    r"^(il giorno .{5,140}(ha scritto|wrote)\s*:?\s*|on .{5,140}wrote\s*:?\s*|"
    r"-{2,}\s*(original message|messaggio originale).*|le .{5,140}a écrit\s*:?\s*|(da|from)\s*:\s.+|"
    # the same line when the mail client wrapped it: it starts with "Il dom 11 ott 2026, 02:17 ..."
    r"(il|on|le)\s+" + _DAYS + r"[ ,.]+\d{1,2}\b.*)$", re.I)


def parse_command(text: str):
    """First non-empty, non-quoted line -> (command, arg) or None. command in off|on|pause."""
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith(">"):
            continue
        words = re.split(r"\s+", s)
        w = words[0].strip(".,:;!?").upper()
        if w in OFF_WORDS:
            return "off", None
        if w in ON_WORDS:
            return "on", None
        if w in PAUSE_WORDS:
            n = 60
            if len(words) > 1 and words[1].isdigit():
                n = min(max(int(words[1]), 1), 1440)
            return "pause", n
        return None  # first real line is not a command (e.g. our own notification, or free text)
    return None


def reply_body(text: str) -> str:
    """What the user actually typed: everything above the quoted original (quote markers, 'ha scritto:',
    divider lines and the reference token are cut)."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith(">") or s.startswith("─") or s == "--" or _QUOTE_INTRO.match(s):
            if out:          # typed text came first: the rest is the quoted original
                break
            continue         # leading quote noise before any typed text
        out.append(line.rstrip())
    body = REF_RE.sub("", "\n".join(out)).strip()
    return body[:MAX_REPLY]


def first_line_of(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip() and not ln.strip().startswith(">")), "")


def _is_reply(msg) -> bool:
    """A real reply, not ccmail's own notification (which also sits in the inbox of a self-addressed mailbox)."""
    return bool(str(msg.get("In-Reply-To") or "").strip()) or bool(_REPLY_SUBJECT.match(str(msg.get("Subject") or "")))


def _text_of(msg) -> str:
    body = None
    if msg.is_multipart():
        body = msg.get_body(preferencelist=("plain",))
    else:
        body = msg
    if body is None:
        return ""
    try:
        return str(body.get_content())[:MAX_TEXT]
    except Exception:
        return ""


def _has_sent_label(fetch_prefix) -> bool:
    """True if a Gmail FETCH response prefix lists the Sent label (X-GM-LABELS (Inbox Sent ...))."""
    raw = fetch_prefix.decode("utf-8", "replace") if isinstance(fetch_prefix, bytes) else str(fetch_prefix)
    m = re.search(r"X-GM-LABELS \((.*?)\)", raw, re.I | re.S)
    return bool(m) and "\\sent" in m.group(1).lower().replace('"', "")


def _msg_time(msg):
    """Sending time from the Date header (epoch seconds) or None if absent/unparseable."""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(str(msg.get("Date"))).timestamp()
    except Exception:
        return None


def sender_ok(msg, expected: str) -> bool:
    return bool(expected) and parseaddr(str(msg.get("From") or ""))[1].strip().lower() == expected.strip().lower()


def authenticated(msg) -> bool:
    ar = " ".join(str(h) for h in (msg.get_all("Authentication-Results") or [])).lower()
    return "dkim=pass" in ar or "spf=pass" in ar


def _target_session(db, ref):
    """Session that sent the mail the user replied to; if the chat moved on, the chat's latest session."""
    sid = db.ref_session(ref)
    if not sid:
        return None
    row = db.one("SELECT chat_id FROM sessions WHERE session_id=?", (sid,))
    if row and row.get("chat_id"):
        latest = db.one("SELECT session_id FROM sessions WHERE chat_id=? ORDER BY last_event_at DESC LIMIT 1",
                        (row["chat_id"],))
        return (latest or {}).get("session_id") or sid
    return sid


def handle_message(db, msg, expected_from: str, now=None, trusted_sent: bool = False,
                   to_claude: bool = False, info: dict = None) -> str:
    """Process one parsed message. Returns the decision string (also stored as an event)."""
    now = now or time.time()
    mid = str(msg.get("Message-ID") or "").strip()[:200] or None

    sent_at = _msg_time(msg)

    def done(decision, summary=""):
        if decision.startswith("ok:") and sent_at is not None and 0 <= now - sent_at < 3600:
            summary = f"{summary[:150]} [arrivata {now - sent_at:.0f} s dopo l'invio]"
        db.add_event(now, "inbound", "InboundCommand", "", summary[:200], decision)
        if mid:
            db.x("INSERT OR IGNORE INTO inbound_seen(msg_id, at) VALUES(?,?)", (mid, now))
        return decision

    if mid and db.one("SELECT 1 FROM inbound_seen WHERE msg_id=?", (mid,)):
        return "duplicate"
    if not sender_ok(msg, expected_from):
        return done("rejected:sender")
    if not (trusted_sent or authenticated(msg)):
        seen = " ".join(str(h) for h in (msg.get_all("Authentication-Results") or []))[:120] or "assente"
        return done("rejected:unauthenticated", f"Authentication-Results: {seen}; etichetta Sent: no")
    text = _text_of(msg)
    cmd = parse_command(text)
    ref = next((r for r in REF_RE.findall(text) if db.lookup_ref(r, now)), None)
    project = db.lookup_ref(ref, now) if ref else None

    if cmd is None:
        first = next((ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(">")), "")
        if first.startswith(("Progetto:", "Project:")) and "Account:" in text[:400]:
            # the body is ccmail's own mail text (an unquoted reply/forward, or our own notification)
            return done("ignored:no_command", "testo di una email di ccmail: nessuna istruzione")
        if not (to_claude and project and _is_reply(msg)):
            if info is not None and _is_reply(msg) and reply_body(text):
                # a verified reply from the user that goes nowhere: say so instead of staying silent
                info.update(kind="rejected", reason="to_claude_off" if project else "no_ref",
                            project_dir=project, ref=ref if project else None, text=reply_body(text))
            return done("ignored:no_command", ("prima riga: " + first[:60]) if first else "")
        body = reply_body(text)
        if not body:
            return done("ignored:empty_reply")
        sid = _target_session(db, ref)
        if not sid:
            if info is not None:
                info.update(kind="rejected", reason="no_session", project_dir=project, ref=ref, text=body)
            return done("rejected:no_session", body[:60])
        db.add_inbound_message(sid, project, body, now)
        if info is not None:
            info.update(kind="claude", session_id=sid, project_dir=project, text=body, ref=ref)
        return done("ok:to_claude", body[:60])

    if not project:
        if info is not None:
            info.update(kind="rejected", reason="no_ref", project_dir=None, ref=None,
                        text=reply_body(text) or first_line_of(text))
        return done("rejected:no_valid_ref")
    if cmd[0] in ("off", "on"):
        row = db.one("SELECT name FROM projects WHERE project_dir=?", (project,))
        name = (row or {}).get("name") or leaf_name(project)
        db.set_project_enabled(project, name, cmd[0] == "on", now)
        if info is not None:
            info.update(kind="command", command=cmd[0], project_name=name, project_dir=project, ref=ref,
                        session_id=db.ref_session(ref),
                        detail=i18n.pick(db.get_settings(),
                                         f"Le notifiche del progetto «{name}» sono state "
                                         + ("riattivate." if cmd[0] == "on" else "disattivate."),
                                         f"Notifications for project «{name}» were turned "
                                         + ("back on." if cmd[0] == "on" else "off.")))
        return done("ok:project_" + cmd[0], f"{cmd[0].upper()} → {name}")
    until = now + cmd[1] * 60
    db.set_settings({"snooze_until": until})
    if info is not None:
        info.update(kind="command", command="pause", project_name="", project_dir=project, ref=ref,
                    session_id=db.ref_session(ref),
                    detail=i18n.pick(db.get_settings(), f"Notifiche in pausa per {cmd[1]} minuti su tutti i progetti.",
                                     f"Notifications paused for {cmd[1]} minutes on all projects."))
    return done("ok:pause", f"PAUSA {cmd[1]} min")


class Inbound(threading.Thread):
    def __init__(self, db):
        super().__init__(daemon=True, name="ccmail-inbound")
        self.db = db
        self.stop_flag = threading.Event()
        self.on_message = None   # set by the daemon: wakes the parked wake-hook of a session
        self.is_parked = None    # set by the daemon: is a wake hook really waiting for that session?
        self.on_enqueue = None   # set by the daemon: sends queued mail right away

    # How often the open connection is asked for news (NOOP). IMAP IDLE was tried and dropped: with Python's
    # buffered sockets a read that times out leaves the connection unusable, and the delay that matters is
    # Gmail's own delivery time (4-25 s measured for mail addressed to oneself), which IDLE cannot shorten.
    FAST_S = 2

    def run(self):
        """Keep one IMAP connection open and ask it for news every FAST_S seconds; scan the mailbox when its
        size changes and, as a safety net, every `inbound_poll_s` seconds. Reconnects on any failure."""
        conn, known, last_full = None, None, 0.0
        while not self.stop_flag.is_set():
            wait = self.FAST_S
            try:
                s = self.db.get_settings()
                if not (s["inbound_enabled"] and s["enabled"]):
                    conn = self._close(conn)
                    self.db.x("DELETE FROM meta WHERE key='inbound_since'")  # re-enabling starts a new window
                    wait = max(self.FAST_S, 15)
                else:
                    if conn is None:
                        conn, known = self._open(s)
                        last_full = 0.0
                        if conn is None:
                            wait = min(s["inbound_poll_s"], 30)  # nothing to watch yet (no credentials / mails)
                    if conn is not None:
                        conn.noop()
                        ex = conn.untagged_responses.pop("EXISTS", None)
                        size = int(ex[-1]) if ex else known
                        changed = size != known
                        prev, known = known, size
                        now = time.time()
                        if changed or now - last_full >= s["inbound_poll_s"]:
                            # new arrivals have the highest sequence numbers: look only at those, unless it
                            # is time for the periodic full pass (safety net against expunges / missed news)
                            full = (not changed) or prev is None or now - last_full >= s["inbound_poll_s"]
                            n = self._scan(conn, s, self._since() - 300, min_seq=0 if full else prev)
                            if changed:  # timing of the quick path, for diagnosing latency (no content)
                                self._meta("inbound_last_change",
                                           f"{now:.2f}|scan_s={time.time() - now:.2f}|new={n}|full={int(full)}")
                            if full:
                                last_full = now
                            self._meta("inbound_last_poll", str(now))
                            self._meta("inbound_last_status",
                                       f"ok: {getattr(self, 'last_found', 0)} email di ccmail nella casella, "
                                       f"{n} nuove esaminate (controllo ogni {self.FAST_S} s)")
            except Exception as e:
                log.exception("inbound poll failed")
                conn = self._close(conn)
                known = None
                self._meta("inbound_last_poll", str(time.time()))
                self._meta("inbound_last_status", "errore: " + type(e).__name__)
                wait = 15
            self.stop_flag.wait(wait)
        self._close(conn)

    @staticmethod
    def _close(conn):
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass
        return None

    def _open(self, s, imap_factory=None):
        """Log in and select the inbox. Returns (connection, mailbox size) or (None, None) if there is
        nothing to watch (no credentials, or no mail with a reference sent in the last 14 days)."""
        cred = credstore.load()
        if not cred:
            return None, None
        if not self.db.one("SELECT 1 FROM mail_refs WHERE created_at>?", (time.time() - 14 * 86400,)):
            return None, None
        host = str(s.get("imap_host", ""))
        factory = imap_factory or (lambda: imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(),
                                                              timeout=20))
        M = factory()
        try:
            M.login(cred[0], cred[1])
            typ, data = M.select("INBOX")
            known = int(data[0]) if typ == "OK" and data and data[0] else None
        except Exception:
            self._close(M)
            raise
        return M, known

    def _acknowledge(self, info: dict) -> None:
        """Queue the instant confirmation mail (the worker sends it within seconds)."""
        from . import ack
        now = time.time()
        s = self.db.get_settings()
        try:
            if info.get("kind") not in ("claude", "command", "rejected"):
                return
            if info.get("kind") == "rejected":
                since = now - 3600  # at most 5 such mails an hour: a broken client cannot flood the mailbox
                if self.db.one("SELECT COUNT(*) n FROM outbox WHERE created_at>? AND subject LIKE ?",
                               (since, "%risposta scartata%"))["n"] >= 5 or \
                        self.db.one("SELECT COUNT(*) n FROM outbox WHERE created_at>? AND subject LIKE ?",
                                    (since, "%reply discarded%"))["n"] >= 5:
                    return
            # every mail we send is repliable: the confirmation gets its own reference, tied to the same
            # project and session as the mail that was answered
            ref = (self.db.new_mail_ref(info.get("project_dir"), now, info.get("session_id"))
                   if info.get("project_dir") else None)
            if info.get("kind") == "rejected":
                row = self.db.one("SELECT name FROM projects WHERE project_dir=?", (info.get("project_dir"),))
                name = (row or {}).get("name") or (leaf_name(info["project_dir"]) if info.get("project_dir") else "")
                subject, body = ack.build_rejection(s, info["reason"], info.get("text", ""), name, ref=ref)
            elif info.get("kind") == "claude":
                sess = self.db.one("SELECT * FROM sessions WHERE session_id=?", (info["session_id"],)) or {}
                parked = self.is_parked(info["session_id"]) if self.is_parked else None
                subject, body = ack.build_claude_ack(self.db, s, info["session_id"], info["text"], now,
                                                     project=sess.get("project") or "",
                                                     chat_title=self.db.chat_title(sess), parked=parked, ref=ref)
            else:
                subject, body = ack.build_command_ack(s, info["command"], info.get("project_name", ""),
                                                      info.get("detail", ""), ref=ref)
            self.db.enqueue(None, subject, body, now)
            if self.on_enqueue:
                self.on_enqueue()
        except Exception:
            log.exception("could not queue the acknowledgement")

    def _meta(self, key, value):
        self.db.x("INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, value))

    def _since(self) -> float:
        """When the channel was switched on (kept in `meta`). Only mails sent after it (minus a margin) count;
        the first time it is set 30 minutes back so a reply sent right before the first pass is not lost."""
        r = self.db.one("SELECT value FROM meta WHERE key='inbound_since'")
        if r:
            return float(r["value"])
        v = time.time() - 30 * 60
        self._meta("inbound_since", str(v))
        return v

    def poll_once(self, s, imap_factory=None, since_ts=0.0) -> int:
        """One pass: connect, scan, disconnect. `imap_factory` is for tests. Returns messages examined."""
        M, _ = self._open(s, imap_factory)
        if M is None:
            return 0
        try:
            return self._scan(M, s, since_ts)
        finally:
            self._close(M)

    @staticmethod
    def _message_ids(M, nums) -> dict:
        """{sequence number (str): Message-ID} for `nums`, fetched with a single command."""
        if not nums:
            return {}
        seq = ",".join(x.decode() if isinstance(x, bytes) else str(x) for x in nums)
        typ, parts = M.fetch(seq, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        out = {}
        if typ != "OK":
            return out
        for part in parts or []:
            if not isinstance(part, tuple):
                continue
            m = re.match(rb"\s*(\d+)\s*\(", part[0])
            if not m:
                continue
            h = email.message_from_bytes(part[1], policy=email_policy.default)
            out[m.group(1).decode()] = str(h.get("Message-ID") or "").strip()[:200]
        return out

    def _scan(self, M, s, since_ts, min_seq: int = 0) -> int:
        """Look at the newest [Claude Code] mails of the open connection (only those with a sequence number
        above `min_seq` when given: the quick path after a new arrival). Returns messages examined."""
        cred = credstore.load()
        user = cred[0] if cred else ""
        expected = s.get("recipient") or user
        host = str(s.get("imap_host", ""))
        gmail = "gmail" in host.lower() or "googlemail" in host.lower()
        n = 0
        since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - 14 * 86400))
        typ, data = M.search(None, f'(SUBJECT "[Claude Code]" SINCE {since})')
        nums = data[0].split() if typ == "OK" and data and data[0] else []
        if min_seq:
            nums = [x for x in nums if int(x) > min_seq]
        else:
            self.last_found = len(nums)
        # SEARCH answers oldest-first: look at the NEWEST ones, and skip what was already handled by
        # fetching only the Message-ID header before the (larger) full message.
        newest_first = list(reversed(nums[-MAX_SCAN:]))
        mids = self._message_ids(M, newest_first)  # ONE round trip for all headers, not one per message
        for num in newest_first:
            hmid = mids.get(num.decode() if isinstance(num, bytes) else str(num), "")
            if hmid and self.db.one("SELECT 1 FROM inbound_seen WHERE msg_id=?", (hmid,)):
                continue
            if n >= MAX_PER_POLL:
                break
            typ, parts = M.fetch(num, "(X-GM-LABELS BODY.PEEK[])" if gmail else "(BODY.PEEK[])")
            if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            sent_label = _has_sent_label(parts[0][0]) if gmail else False
            msg = email.message_from_bytes(parts[0][1], policy=email_policy.default)
            mid = str(msg.get("Message-ID") or "").strip()[:200]
            if mid and self.db.one("SELECT 1 FROM inbound_seen WHERE msg_id=?", (mid,)):
                continue  # already handled in an earlier pass
            sent = _msg_time(msg)
            if sent is not None and sent < since_ts:
                if mid:  # older than the switch-on moment: remember it, never act on it
                    self.db.x("INSERT OR IGNORE INTO inbound_seen(msg_id, at) VALUES(?,?)", (mid, time.time()))
                continue
            n += 1
            info = {}
            decision = handle_message(self.db, msg, expected, trusted_sent=sent_label,
                                      to_claude=bool(s.get("inbound_to_claude")), info=info)
            if (decision.startswith("ok:") and s.get("inbound_ack", True)) or info.get("kind") == "rejected":
                self._acknowledge(info)
            if decision.startswith("ok:"):
                M.store(num, "+FLAGS", "\\Seen")  # consumed: don't leave a stale unread reply
            if decision == "ok:to_claude" and self.on_message:
                for row in self.db.q("SELECT DISTINCT session_id FROM inbound_messages WHERE delivered_at IS NULL"):
                    self.on_message(row["session_id"])
        return n
