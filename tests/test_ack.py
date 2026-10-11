import time
import unittest
from unittest import mock

from ccmail import ack, credstore, inbound
from helpers import TmpHome
from test_inbound import ME, FakeIMAP, reply

NOW = 1_800_000_000.0


def turns(db, sid, durations, start=NOW - 5000):
    t = start
    for d in durations:
        db.add_event(t, sid, "UserPromptSubmit", "", "", "recorded")
        db.add_event(t + d, sid, "Stop", "", "", "skip:short_turn")
        t += d + 60


class EtaTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.db.touch_session("s1", "C:/w/demo", NOW)

    def test_typical_turn_is_the_median_of_recent_turns(self):
        turns(self.db, "s1", [60, 120, 600])
        self.assertEqual(ack.typical_turn_s(self.db, "s1"), 120)

    def test_working_chat_gets_a_countdown_from_the_typical_turn(self):
        turns(self.db, "s1", [300, 300, 300])
        self.db.set_session("s1", state="active", turn_start_at=NOW - 120)
        e = ack.eta(self.db, "s1", NOW)
        self.assertIn("sta lavorando", e["state"])
        self.assertIn("prossimo passo", e["when"])    # in-turn delivery: no waiting for the end of the turn
        self.assertIn("circa 5 min", e["typical"])

    def test_longer_than_usual_turn_is_said_honestly(self):
        turns(self.db, "s1", [60, 60])
        self.db.set_session("s1", state="active", turn_start_at=NOW - 900)
        self.assertIn("già più lungo del solito", ack.eta(self.db, "s1", NOW)["when"])

    def test_no_history_means_no_invented_estimate(self):
        self.db.set_session("s1", state="active", turn_start_at=NOW - 30)
        e = ack.eta(self.db, "s1", NOW)
        self.assertIsNone(e["typical"])
        self.assertIn("prossimo passo", e["when"])

    def test_idle_waiting_and_background_and_ended_and_cold(self):
        self.db.set_session("s1", state="idle", last_event_at=NOW - 30)
        listening = ack.eta(self.db, "s1", NOW, parked=True)
        self.assertIn("pochi secondi", listening["when"])
        self.assertIn("in ascolto", listening["state"])
        self.db.set_session("s1", bg_tasks=2)
        self.assertIn("processi in background", ack.eta(self.db, "s1", NOW, parked=True)["state"])
        self.db.set_session("s1", bg_tasks=0)
        self.db.set_session("s1", state="ended")
        self.assertIn("chiusa", ack.eta(self.db, "s1", NOW)["state"])
        self.db.set_session("s1", state="idle", bg_tasks=0, last_event_at=NOW - 5 * 3600)
        self.assertIn("ferma da", ack.eta(self.db, "s1", NOW)["state"])
        self.assertIn("prossimo evento", ack.eta(self.db, "s1", NOW)["when"])


class HonestEtaTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.db.touch_session("s1", "C:/w/demo", NOW)
        self.db.set_session("s1", state="idle", last_event_at=NOW - 20)

    def test_not_parked_never_promises_seconds(self):
        e = ack.eta(self.db, "s1", NOW, parked=False)
        self.assertNotIn("pochi secondi", e["when"])
        self.assertIn("non è in ascolto", e["state"])
        self.assertIn("prossimo evento", e["when"])

    def test_unknown_listening_state_is_conditional(self):
        e = ack.eta(self.db, "s1", NOW, parked=None)
        self.assertIn("se la chat è in ascolto", e["when"])
        self.assertNotIn("pochi secondi", e["when"])


class AckMailTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.db.touch_session("sess-A", "C:/w/demo", time.time())
        self.db.set_session("sess-A", state="idle", last_event_at=time.time())
        self.ref = self.db.new_mail_ref("C:/w/demo", time.time(), "sess-A")
        self.db.set_settings({"inbound_enabled": True, "inbound_to_claude": True})

    def poll(self, msgs, **settings):
        fake = FakeIMAP([m.as_bytes() for m in msgs])
        s = {"imap_host": "x", "recipient": "", "inbound_to_claude": True, "inbound_ack": True, **settings}
        with mock.patch.object(credstore, "load", return_value=(ME, "pw", "t")):
            w = inbound.Inbound(self.db)
            w.is_parked = lambda sid: True
            return w.poll_once(s, imap_factory=lambda: fake, since_ts=0)

    def threaded(self, text, mid):
        m = reply(text, self.ref, mid=mid)
        m["In-Reply-To"] = "<orig@x>"
        return m

    def acks(self):
        return self.db.q("SELECT subject, body FROM outbox ORDER BY id")

    def test_accepted_reply_is_acknowledged_with_state_and_time(self):
        self.poll([self.threaded("Procedi con la prima opzione.", "<a1>")])
        a = self.acks()
        self.assertEqual(len(a), 1)
        self.assertIn("risposta ricevuta e messa in coda", a[0]["subject"])
        self.assertIn("Procedi con la prima opzione.", a[0]["body"])
        self.assertIn("QUANDO ARRIVA", a[0]["body"])
        self.assertIn("Stato: Claude è fermo", a[0]["body"])
        # the confirmation is itself repliable: it carries its own reference, tied to the same session/project
        self.assertIn("Rif: ccm-", a[0]["body"])
        ref = a[0]["body"].split("Rif: ")[1].split()[0]
        self.assertEqual(self.db.ref_session(ref), "sess-A")
        self.assertEqual(self.db.lookup_ref(ref, time.time()), "c:/w/demo")

    def test_replying_to_the_confirmation_works_like_replying_to_the_notification(self):
        """Regression: the latest mail in the inbox is usually the confirmation; replies to it were ignored."""
        self.poll([self.threaded("Primo messaggio", "<q1>")])
        body = self.acks()[0]["body"]
        ack_ref = body.split("Rif: ")[1].split()[0]
        second = reply("è sempre in ascolto claude? Fa consumare token?", ack_ref, mid="<q2>")
        second["In-Reply-To"] = "<ack@x>"
        self.assertEqual(inbound.handle_message(self.db, second, ME, now=time.time(), to_claude=True),
                         "ok:to_claude")
        texts = [m["text"] for m in self.db.pending_inbound("sess-A", time.time())]
        self.assertIn("è sempre in ascolto claude? Fa consumare token?", texts)
        off = reply("OFF", ack_ref, mid="<q3>")
        self.assertEqual(inbound.handle_message(self.db, off, ME, now=time.time(), to_claude=True),
                         "ok:project_off")
        self.assertFalse(self.db.project_enabled("C:/w/demo"))

    def test_command_ack_is_repliable_and_watchdog_mail_too(self):
        self.poll([reply("PAUSA 5", self.ref, mid="<c9>")])
        self.assertIn("Rif: ccm-", self.acks()[0]["body"])
        from ccmail import render
        subject, body = render.build_watchdog({"inbound_to_claude": False, "account_name": "X"}, "demo", "sess-A",
                                              15, 1, ["b1"], ref="ccm-0123456789ab")
        self.assertIn("Rif: ccm-0123456789ab", body)

    def test_command_is_acknowledged_too(self):
        self.poll([reply("OFF", self.ref, mid="<c1>")])
        a = self.acks()
        self.assertEqual(len(a), 1)
        self.assertIn("comando eseguito", a[0]["subject"])
        self.assertIn("disattivate", a[0]["body"])

    def test_rejected_or_ignored_messages_get_no_ack(self):
        evil = reply("OFF", self.ref, sender="evil@example.com", mid="<e>")
        own = chr(10).join(["Progetto: demo", "Account: alice", "Chat: x"])  # ccmail's own mail text, unquoted
        prose = reply(own, self.ref, mid="<p>")
        self.poll([evil, prose])
        self.assertEqual(self.acks(), [])

    def test_switch_turns_the_confirmation_off(self):
        self.poll([self.threaded("ciao", "<s1>")], inbound_ack=False)
        self.assertEqual(self.acks(), [])

    def test_our_own_ack_is_never_taken_for_a_reply(self):
        self.poll([self.threaded("ciao", "<s2>")])
        subject, body = self.acks()[0]["subject"], self.acks()[0]["body"]
        m = reply(body.splitlines()[0], self.ref, mid="<loop>")
        m.replace_header("Subject", subject)       # as it would come back from the inbox
        with mock.patch.object(credstore, "load", return_value=(ME, "pw", "t")):
            self.assertEqual(inbound.handle_message(self.db, m, ME, now=time.time(), to_claude=True),
                             "ignored:no_command")

    def test_delivery_is_recorded_in_the_history(self):
        from ccmail import server
        self.db.add_inbound_message("sess-A", "c:/w/demo", "x", time.time())
        # exercise the same code path the server uses when it hands the message to the hook
        pend = self.db.pending_inbound("sess-A", time.time())
        self.db.mark_inbound_delivered([p["id"] for p in pend], time.time())
        self.assertEqual(self.db.pending_inbound("sess-A", time.time()), [])


if __name__ == "__main__":
    unittest.main()
