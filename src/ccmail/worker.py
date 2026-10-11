"""Outbox worker (retry with backoff) and optional silent-session watchdog."""
import json
import logging
import threading
import time

from . import chats, config, mailer, render

log = logging.getLogger("ccmail.worker")
MAX_ATTEMPTS = 5


class Worker(threading.Thread):
    def __init__(self, db):
        super().__init__(daemon=True, name="ccmail-worker")
        self.db = db
        self.wake = threading.Event()
        self.stop_flag = threading.Event()
        self._last_watchdog = 0.0

    def run(self):
        while not self.stop_flag.is_set():
            try:
                self.step()
                self.watchdog()
            except Exception:
                log.exception("worker step failed")
            self.wake.wait(5)
            self.wake.clear()
            try:
                chats.scan(self.db)  # rate-limited internally; keeps chat names and projects fresh
            except Exception:
                log.exception("chat scan failed")

    def step(self, now=None):
        now = now or time.time()
        settings = self.db.get_settings()
        for row in self.db.due_outbox(now):
            try:
                mailer.send(settings, row["subject"], row["body"])
            except mailer.MailError as e:
                n = row["attempts"] + 1
                if n >= MAX_ATTEMPTS:
                    self.db.x("UPDATE outbox SET status='failed', attempts=?, last_error=? WHERE id=?",
                              (n, str(e), row["id"]))
                else:
                    self.db.x("UPDATE outbox SET attempts=?, last_error=?, next_try_at=? WHERE id=?",
                              (n, str(e), now + 30 * 2 ** n, row["id"]))
                log.warning("send failed (attempt %d): %s", n, e)
            else:
                self.db.x("UPDATE outbox SET status='sent', sent_at=?, last_error=NULL WHERE id=?", (now, row["id"]))

    def watchdog(self, now=None):
        """One mail per idle period for sessions silent while background tasks were still running."""
        now = now or time.time()
        if now - self._last_watchdog < 60:
            return
        self._last_watchdog = now
        s = self.db.get_settings()
        mins = s["watchdog_minutes"]
        if not (s["enabled"] and mins):
            return
        if config.check_combo(s):  # would fire before a heartbeat can: stay quiet until the user fixes it
            return
        if now < (s.get("snooze_until") or 0):
            return
        rows = self.db.q("SELECT * FROM sessions WHERE state='idle' AND bg_tasks>0 AND watchdog_sent=0 "
                         "AND last_event_at<?", (now - mins * 60,))
        for r in rows:
            if self.db.sent_last_hour(now) >= s["max_per_hour"]:
                break
            if r.get("last_sent_at") and now - r["last_sent_at"] < mins * 60:
                continue  # a mail about this session went out recently: it is not silent for you
            if not self.db.project_enabled(r.get("project_dir")):
                continue
            ref = (self.db.new_mail_ref(r.get("project_dir"), now, r["session_id"])
                   if s.get("inbound_enabled") else None)
            subject, body = render.build_watchdog(s, r.get("project") or "", r["session_id"], mins,
                                                  r["bg_tasks"], json.loads(r.get("bg_ids") or "[]"),
                                                  chat_title=self.db.chat_title(r), ref=ref)
            self.db.enqueue(None, subject, body, now)
            self.db.set_session(r["session_id"], watchdog_sent=1, last_sent_at=now)
