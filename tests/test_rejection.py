"""A verified reply that goes nowhere is answered with a mail in the usual layout whose OUTCOME says why."""
import time
import unittest
from unittest import mock

from ccmail import ack, credstore, inbound
from helpers import TmpHome
from test_inbound import ME, FakeIMAP, reply

PROJECT = "c:/x/demo"


class RejectionTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.enable(time.time() - 1000)
        self.db.set_settings({"inbound_enabled": True, "inbound_to_claude": True})

    def handle(self, msg, to_claude=True):
        info = {}
        decision = inbound.handle_message(self.db, msg, ME, trusted_sent=True, to_claude=to_claude, info=info)
        return decision, info

    def test_no_session_is_reported(self):
        ref = self.db.new_mail_ref(PROJECT, time.time(), None)  # a reference tied to no chat
        decision, info = self.handle(reply("Ok perfetto, vai avanti", ref))
        self.assertEqual(decision, "rejected:no_session")
        self.assertEqual((info["kind"], info["reason"]), ("rejected", "no_session"))
        subject, body = ack.build_rejection(self.db.get_settings(), "no_session", info["text"], "demo", ref="ccm-0123456789ab")
        self.assertIn("risposta scartata", subject)
        self.assertTrue(body.startswith("Progetto: demo"))  # the loop guard recognises ccmail's own text
        self.assertIn("ESITO", body)
        self.assertIn("Risposta scartata: non so a quale chat", body)
        self.assertIn("Ok perfetto, vai avanti", body)

    def test_unknown_reference_is_reported(self):
        decision, info = self.handle(reply("Ok perfetto", "ccm-ffffffffffff"))
        self.assertEqual(decision, "ignored:no_command")
        self.assertEqual(info["reason"], "no_ref")

    def test_delivery_to_claude_off_is_reported(self):
        ref = self.db.new_mail_ref(PROJECT, time.time(), "s1")
        decision, info = self.handle(reply("Ok perfetto", ref), to_claude=False)
        self.assertEqual(info["reason"], "to_claude_off")
        self.assertEqual(info["ref"], ref)

    def test_command_with_unknown_reference_is_reported(self):
        decision, info = self.handle(reply("OFF", "ccm-ffffffffffff"))
        self.assertEqual(decision, "rejected:no_valid_ref")
        self.assertEqual(info["reason"], "no_ref")

    def test_silent_for_ccmail_text_unverified_and_non_replies(self):
        ref = self.db.new_mail_ref(PROJECT, time.time(), "s1")
        own = reply("Progetto: demo\nAccount: Alice\nChat: x", ref)
        self.assertEqual(self.handle(own)[1], {})
        self.assertEqual(self.handle(reply("Ok", ref, sender="evil@x.com", mid="<e@x>"))[1], {})
        info = {}
        inbound.handle_message(self.db, reply("Ok", "ccm-ffffffffffff", auth=None, mid="<u@x>"), ME,
                               trusted_sent=False, to_claude=True, info=info)
        self.assertEqual(info, {})

    def test_poll_queues_one_mail_and_not_for_its_own_copy(self):
        ref = self.db.new_mail_ref(PROJECT, time.time(), None)
        with mock.patch.object(credstore, "load", return_value=(ME, "pw", "t")):
            inbound.Inbound(self.db).poll_once({"imap_host": "imap.example.com", "recipient": ""},
                                               imap_factory=lambda: FakeIMAP([reply("Ok perfetto", ref).as_bytes()]),
                                               since_ts=0)
        rows = self.db.q("SELECT subject, body FROM outbox")
        self.assertEqual(len(rows), 1)
        self.assertIn("risposta scartata", rows[0]["subject"])
        # the mail we just queued, as it will look in the inbox, is ignored: no loop
        mine = reply(rows[0]["body"], "", mid="<own@x>", quoted=False)
        self.assertEqual(self.handle(mine)[1], {})

    def test_hourly_cap(self):
        ref = self.db.new_mail_ref(PROJECT, time.time(), None)
        ib = inbound.Inbound(self.db)
        for i in range(8):
            ib._acknowledge({"kind": "rejected", "reason": "no_session", "project_dir": PROJECT, "ref": ref,
                             "text": f"t{i}"})
        self.assertEqual(len(self.db.q("SELECT 1 FROM outbox")), 5)

    def test_english(self):
        s = {**self.db.get_settings(), "language": "en"}
        subject, body = ack.build_rejection(s, "to_claude_off", "hi", "demo")
        self.assertIn("reply discarded", subject)
        self.assertIn("Reply discarded:", body)
        self.assertNotIn("ESITO", body)


if __name__ == "__main__":
    unittest.main()
