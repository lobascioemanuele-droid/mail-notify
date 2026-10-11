import time
import unittest
from email.message import EmailMessage
from unittest import mock

from ccmail import credstore, inbound
from helpers import TmpHome

NOW = 1_800_000_000.0
ME = "alice@gmail.com"


def reply(first_line="OFF", ref=None, sender=ME, auth="mx.google.com; dkim=pass header.i=@gmail.com; spf=pass",
          mid="<m1@x>", quoted=True):
    m = EmailMessage()
    m["From"] = f"Alice <{sender}>"
    m["To"] = ME
    m["Subject"] = "Re: [Claude Code] demo: task completato"
    m["Message-ID"] = mid
    if auth is not None:
        m["Authentication-Results"] = auth
    body = first_line + "\n\n"
    if quoted:
        body += "> Progetto: demo\n> Rif: " + (ref or "") + "\n"
    m.set_content(body)
    return m


class ParseTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(inbound.parse_command("OFF\n> quoted"), ("off", None))
        self.assertEqual(inbound.parse_command("  spegni.  "), ("off", None))
        self.assertEqual(inbound.parse_command("\n\nAccendi"), ("on", None))
        self.assertEqual(inbound.parse_command("PAUSA 90"), ("pause", 90))
        self.assertEqual(inbound.parse_command("pausa"), ("pause", 60))
        self.assertEqual(inbound.parse_command("PAUSA 99999"), ("pause", 1440))

    def test_only_the_first_real_line_counts(self):
        self.assertIsNone(inbound.parse_command("Progetto: demo\nOFF"))      # our own notification
        self.assertIsNone(inbound.parse_command("> OFF\nciao"))              # quoted command
        self.assertIsNone(inbound.parse_command("Per favore spegni tutto"))  # prose is never a command
        self.assertIsNone(inbound.parse_command(""))


class HandleTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.db.touch_session("s1", "C:/w/demo", NOW)
        self.db.set_project_enabled("C:/w/demo", "demo", True, NOW)
        self.ref = self.db.new_mail_ref("C:/w/demo", NOW - 60)

    def h(self, msg):
        return inbound.handle_message(self.db, msg, ME, now=NOW)

    def test_off_and_on_roundtrip(self):
        self.assertEqual(self.h(reply("OFF", self.ref, mid="<a>")), "ok:project_off")
        self.assertFalse(self.db.project_enabled("C:/w/demo"))
        self.assertEqual(self.h(reply("ON", self.ref, mid="<b>")), "ok:project_on")
        self.assertTrue(self.db.project_enabled("C:/w/demo"))

    def test_pause_sets_global_snooze(self):
        self.assertEqual(self.h(reply("PAUSA 30", self.ref)), "ok:pause")
        self.assertAlmostEqual(self.db.get_settings()["snooze_until"], NOW + 1800)

    def test_rejections_change_nothing(self):
        cases = [
            (reply("OFF", self.ref, sender="evil@example.com", mid="<1>"), "rejected:sender"),
            (reply("OFF", self.ref, auth=None, mid="<2>"), "rejected:unauthenticated"),
            (reply("OFF", self.ref, auth="mx; dkim=fail; spf=fail", mid="<3>"), "rejected:unauthenticated"),
            (reply("OFF", "ccm-000000000000", mid="<4>"), "rejected:no_valid_ref"),
            (reply("OFF", None, mid="<5>"), "rejected:no_valid_ref"),
            (reply("Progetto: demo", self.ref, mid="<6>"), "ignored:no_command"),
        ]
        for msg, want in cases:
            self.assertEqual(self.h(msg), want)
        self.assertTrue(self.db.project_enabled("C:/w/demo"))

    def test_old_reference_expires_and_messages_are_not_replayed(self):
        old = self.db.new_mail_ref("C:/w/demo", NOW - 15 * 86400)
        self.assertEqual(self.h(reply("OFF", old, mid="<o>")), "rejected:no_valid_ref")
        self.assertEqual(self.h(reply("OFF", self.ref, mid="<r>")), "ok:project_off")
        self.db.set_project_enabled("C:/w/demo", "demo", True, NOW)
        self.assertEqual(self.h(reply("OFF", self.ref, mid="<r>")), "duplicate")
        self.assertTrue(self.db.project_enabled("C:/w/demo"))

    def test_actions_are_logged_as_events(self):
        self.h(reply("OFF", self.ref))
        e = self.db.one("SELECT event, decision FROM events WHERE event='InboundCommand'")
        self.assertEqual(e["decision"], "ok:project_off")


class FakeIMAP:
    def __init__(self, raw_messages):
        self.raw, self.stored, self.closed = raw_messages, [], False

    def login(self, u, p): self.logged = (u, p)
    def select(self, box): return ("OK", [b"1"])
    def search(self, *a): return ("OK", [b" ".join(str(i + 1).encode() for i in range(len(self.raw)))])
    def fetch(self, num, what):
        raw = self.raw[int(num) - 1]
        if "HEADER.FIELDS" in what:
            import email as _e
            mid = _e.message_from_bytes(raw).get("Message-ID", "")
            header = ("Message-ID: " + str(mid) + chr(13) + chr(10) + chr(13) + chr(10)).encode()
            return ("OK", [(b"1 (BODY[HEADER.FIELDS (MESSAGE-ID)] {0})", header), b")"])
        return ("OK", [(b"1 (BODY[] {0})", raw), b")"])
    def store(self, num, *a): self.stored.append(num)
    def logout(self): self.closed = True


class PollTests(TmpHome):
    def test_poll_applies_valid_reply_and_marks_it_read(self):
        self.db.touch_session("s1", "C:/w/demo", time.time())
        ref = self.db.new_mail_ref("C:/w/demo", time.time())
        fake = FakeIMAP([reply("OFF", ref).as_bytes(), reply("hello", ref, sender="x@y.z", mid="<z>").as_bytes()])
        with mock.patch.object(credstore, "load", return_value=(ME, "pw", "test")):
            n = inbound.Inbound(self.db).poll_once({"imap_host": "x", "recipient": ""}, imap_factory=lambda: fake)
        self.assertEqual(n, 2)
        self.assertFalse(self.db.project_enabled("C:/w/demo"))
        self.assertEqual(fake.stored, [b"1"])  # only the accepted command is marked read
        self.assertTrue(fake.closed)

    def test_no_credentials_or_no_sent_refs_means_no_connection(self):
        boom = mock.Mock(side_effect=AssertionError("must not connect"))
        with mock.patch.object(credstore, "load", return_value=None):
            self.assertEqual(inbound.Inbound(self.db).poll_once({"imap_host": "x"}, imap_factory=boom), 0)
        with mock.patch.object(credstore, "load", return_value=(ME, "pw", "t")):
            self.assertEqual(inbound.Inbound(self.db).poll_once({"imap_host": "x"}, imap_factory=boom), 0)


if __name__ == "__main__":
    unittest.main()


class WindowTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.db.touch_session("s1", "C:/w/demo", time.time())
        self.ref = self.db.new_mail_ref("C:/w/demo", time.time())

    def run_poll(self, msgs, since_ts):
        fake = FakeIMAP([m.as_bytes() for m in msgs])
        with mock.patch.object(credstore, "load", return_value=(ME, "pw", "test")):
            return inbound.Inbound(self.db).poll_once({"imap_host": "x", "recipient": ""},
                                                      imap_factory=lambda: fake, since_ts=since_ts)

    def dated(self, first, mid, when):
        from email.utils import formatdate
        m = reply(first, self.ref, mid=mid)
        m["Date"] = formatdate(when, usegmt=True)
        return m

    def test_old_reply_is_never_applied_late(self):
        now = time.time()
        old = self.dated("OFF", "<old>", now - 3 * 3600)
        new = self.dated("PAUSA 5", "<new>", now - 60)
        n = self.run_poll([old, new], since_ts=now - 1800)
        self.assertEqual(n, 1)                                   # only the recent one was examined
        self.assertTrue(self.db.project_enabled("C:/w/demo"))    # the old OFF did nothing
        self.assertIsNotNone(self.db.one("SELECT 1 FROM inbound_seen WHERE msg_id='<old>'"))
        self.assertGreater(self.db.get_settings()["snooze_until"], now)

    def test_already_read_replies_are_found_and_not_replayed(self):
        now = time.time()
        msg = self.dated("OFF", "<r1>", now - 30)
        self.assertEqual(self.run_poll([msg], since_ts=now - 600), 1)
        self.assertFalse(self.db.project_enabled("C:/w/demo"))
        self.db.set_project_enabled("C:/w/demo", "demo", True, now)
        self.assertEqual(self.run_poll([msg], since_ts=now - 600), 0)  # same Message-ID: skipped
        self.assertTrue(self.db.project_enabled("C:/w/demo"))

    def test_loop_records_status_and_window(self):
        db = self.db
        db.set_settings({"inbound_enabled": True, "enabled": True})
        w = inbound.Inbound(db)
        since = w._since()
        self.assertAlmostEqual(since, time.time() - 1800, delta=5)
        self.assertEqual(w._since(), since)  # stable across passes


class SentLabelTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.db.touch_session("s1", "C:/w/demo", NOW)
        self.ref = self.db.new_mail_ref("C:/w/demo", NOW - 60)

    def test_label_detection(self):
        self.assertTrue(inbound._has_sent_label(b'3 (X-GM-LABELS (\Inbox \Sent "Etichetta") BODY[] {10}'))
        self.assertTrue(inbound._has_sent_label(b'3 (X-GM-LABELS ("\\Sent") BODY[] {10}'))
        self.assertFalse(inbound._has_sent_label(b'3 (X-GM-LABELS (\Inbox \Important) BODY[] {10}'))
        self.assertFalse(inbound._has_sent_label(b'3 (BODY[] {10}'))

    def test_self_sent_mail_without_auth_header_is_accepted_via_label(self):
        msg = reply("OFF", self.ref, auth=None, mid="<self>")
        self.assertEqual(inbound.handle_message(self.db, msg, ME, now=NOW), "rejected:unauthenticated")
        msg2 = reply("OFF", self.ref, auth=None, mid="<self2>")
        self.assertEqual(inbound.handle_message(self.db, msg2, ME, now=NOW, trusted_sent=True), "ok:project_off")

    def test_label_never_overrides_the_sender_or_reference_checks(self):
        spoof = reply("OFF", self.ref, sender="evil@example.com", auth=None, mid="<s>")
        self.assertEqual(inbound.handle_message(self.db, spoof, ME, now=NOW, trusted_sent=True), "rejected:sender")
        noref = reply("OFF", "ccm-000000000000", auth=None, mid="<n>")
        self.assertEqual(inbound.handle_message(self.db, noref, ME, now=NOW, trusted_sent=True),
                         "rejected:no_valid_ref")

    def test_rejection_records_what_was_seen(self):
        inbound.handle_message(self.db, reply("OFF", self.ref, auth=None, mid="<d>"), ME, now=NOW)
        e = self.db.one("SELECT summary FROM events WHERE event='InboundCommand'")
        self.assertIn("Authentication-Results: assente", e["summary"])

    def test_poll_uses_label_from_the_fetch_response(self):
        raw = reply("OFF", self.ref, auth=None, mid="<p>").as_bytes()

        class Labelled(FakeIMAP):
            def fetch(self, num, what):
                if "HEADER.FIELDS" in what:
                    return super().fetch(num, what)
                assert "X-GM-LABELS" in what          # Gmail host: the label is requested
                return ("OK", [(b'1 (X-GM-LABELS (\Inbox \Sent) BODY[] {9}', self.raw[0]), b")"])
        fake = Labelled([raw])
        with mock.patch.object(credstore, "load", return_value=(ME, "pw", "t")):
            inbound.Inbound(self.db).poll_once({"imap_host": "imap.gmail.com", "recipient": ""},
                                               imap_factory=lambda: fake, since_ts=0)
        self.assertFalse(self.db.project_enabled("C:/w/demo"))


class ReplyToClaudeTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.db.touch_session("sess-A", "C:/w/demo", NOW)
        self.db.set_project_enabled("C:/w/demo", "demo", True, NOW)
        self.ref = self.db.new_mail_ref("C:/w/demo", NOW - 60, "sess-A")

    def threaded(self, text, **kw):
        m = reply(text, self.ref, **kw)
        m["In-Reply-To"] = "<orig@ccmail>"
        return m

    def h(self, msg, to_claude=True):
        return inbound.handle_message(self.db, msg, ME, now=NOW, to_claude=to_claude)

    def test_free_text_is_queued_for_the_session_that_sent_the_mail(self):
        d = self.h(self.threaded("Procedi con la prima opzione e poi lancia i test.", mid="<f1>"))
        self.assertEqual(d, "ok:to_claude")
        p = self.db.pending_inbound("sess-A", NOW)
        self.assertEqual([x["text"] for x in p], ["Procedi con la prima opzione e poi lancia i test."])

    def test_off_by_default(self):
        d = self.h(self.threaded("Procedi.", mid="<f2>"), to_claude=False)
        self.assertEqual(d, "ignored:no_command")
        self.assertEqual(self.db.pending_inbound("sess-A", NOW), [])
        e = self.db.one("SELECT summary FROM events ORDER BY id DESC LIMIT 1")
        self.assertIn("Procedi", e["summary"])  # the first line is recorded to explain the decision

    def test_own_notification_is_never_delivered(self):
        m = reply("Progetto: demo\nChat: x", self.ref, mid="<n>")   # no In-Reply-To, subject without Re:
        m.replace_header("Subject", "[Claude Code] demo: task completato")
        self.assertEqual(self.h(m), "ignored:no_command")
        self.assertEqual(self.db.pending_inbound("sess-A", NOW), [])

    def test_quoted_original_and_reference_are_stripped(self):
        body = ("Fai così: usa il fold 3.\nGrazie\n\nIl giorno sab 10 ott 2026 alle 20:30 Claude Code <a@b> ha scritto:\n"
                "> Progetto: demo\n> Rif: " + self.ref + "\n")
        self.assertEqual(inbound.reply_body(body), "Fai così: usa il fold 3.\nGrazie")
        self.assertEqual(inbound.reply_body("> solo citazione\n> Rif: " + self.ref), "")
        self.assertEqual(len(inbound.reply_body("x" * 5000)), inbound.MAX_REPLY)

    def test_security_checks_still_apply_to_free_text(self):
        self.assertEqual(self.h(self.threaded("ordina X", sender="evil@example.com", mid="<e1>")), "rejected:sender")
        self.assertEqual(self.h(self.threaded("ordina X", auth=None, mid="<e2>")), "rejected:unauthenticated")
        bad = reply("ordina X", "ccm-000000000000", mid="<e3>")
        bad["In-Reply-To"] = "<x>"
        self.assertEqual(self.h(bad), "ignored:no_command")  # unknown reference: nothing delivered
        self.assertEqual(self.db.pending_inbound("sess-A", NOW), [])

    def test_delivery_goes_to_the_latest_session_of_the_same_chat(self):
        self.add_chat("local_1", "sess-A", "Chat", "C:/w/demo")
        self.db.touch_session("sess-B", "C:/w/demo", NOW + 5)
        self.db.set_session("sess-B", chat_id="local_1")
        self.db.set_session("sess-A", chat_id="local_1")
        self.assertEqual(self.h(self.threaded("continua", mid="<f3>")), "ok:to_claude")
        self.assertEqual(len(self.db.pending_inbound("sess-B", NOW)), 1)

    def test_old_messages_expire(self):
        self.db.add_inbound_message("sess-A", "c:/w/demo", "vecchio", NOW - 3 * 86400)
        self.assertEqual(self.db.pending_inbound("sess-A", NOW), [])


class ManyMessagesTests(TmpHome):
    """Regression: the newest replies were never examined once more than MAX_PER_POLL mails matched."""

    def test_a_new_reply_after_many_old_mails_is_found(self):
        self.db.touch_session("s1", "C:/w/demo", time.time())
        ref = self.db.new_mail_ref("C:/w/demo", time.time(), "s1")
        old = []
        for i in range(60):  # 60 earlier notifications, all already processed
            m = reply("Progetto: demo", ref, mid=f"<old{i}>")
            old.append(m.as_bytes())
            self.db.x("INSERT INTO inbound_seen(msg_id, at) VALUES(?,?)", (f"<old{i}>", time.time()))
        newest = reply("OFF", ref, mid="<newest>").as_bytes()
        fake = FakeIMAP(old + [newest])
        w = inbound.Inbound(self.db)
        with mock.patch.object(credstore, "load", return_value=(ME, "pw", "t")):
            n = w.poll_once({"imap_host": "x", "recipient": ""}, imap_factory=lambda: fake, since_ts=0)
        self.assertEqual(n, 1)
        self.assertFalse(self.db.project_enabled("C:/w/demo"))
        self.assertEqual(w.last_found, 61)
