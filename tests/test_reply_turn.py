"""A turn started by an e-mail reply is always answered by e-mail, even when it is short or processes still run."""
import time
import unittest

from ccmail import policy, server
from helpers import TmpHome

T0 = 1_000_000.0


def prompt(sid="s1"):
    return {"hook_event_name": "UserPromptSubmit", "session_id": sid, "cwd": "C:/x/proj"}


def stop(sid="s1", msg="Ho fatto i controlli: tutto coerente.", **kw):
    return {"hook_event_name": "Stop", "session_id": sid, "cwd": "C:/x/proj", "last_assistant_message": msg, **kw}


class ReplyTurnTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.enable(T0 - 1000)
        policy.handle_event(self.db, prompt(), now=T0 - 500)  # the session exists
        self.db.set_session("s1", turn_start_at=None, state="idle")

    def queue_reply(self):
        """An accepted e-mail reply is queued and handed to the chat (as the wake hook would)."""
        self.db.add_inbound_message("s1", "c:/x/proj", "Controlla i risultati", time.time())
        self.assertIsNotNone(server.take_pending_replies(self.db, "s1", "wake"))

    def test_short_answer_to_a_reply_is_mailed(self):
        self.queue_reply()
        policy.handle_event(self.db, prompt(), now=T0 + 2)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 20), "queued:reply")
        self.assertIn("risposta alla tua email", self.db.q("SELECT subject FROM outbox")[-1]["subject"])

    def test_flag_is_one_shot(self):
        self.queue_reply()
        policy.handle_event(self.db, prompt(), now=T0 + 2)
        policy.handle_event(self.db, stop(), now=T0 + 20)
        policy.handle_event(self.db, prompt(), now=T0 + 200)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 220), "skip:short_turn")

    def test_ordinary_short_turn_still_silent(self):
        policy.handle_event(self.db, prompt(), now=T0)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 20), "skip:short_turn")

    def test_reply_answer_ignores_running_background_tasks(self):
        self.queue_reply()
        policy.handle_event(self.db, prompt(), now=T0 + 2)
        bg = stop(background_tasks=[{"id": "t1"}])
        self.assertEqual(policy.handle_event(self.db, bg, now=T0 + 20), "queued:reply")

    def test_reply_turn_ends_with_stop_hook_active_and_is_still_mailed(self):
        """The reply reaches the chat through a Stop hook, so its Stop arrives with stop_hook_active=true."""
        self.queue_reply()
        policy.handle_event(self.db, prompt(), now=T0 + 2)
        self.assertEqual(policy.handle_event(self.db, stop(stop_hook_active=True), now=T0 + 20), "queued:reply")

    def test_stop_hook_loop_guard_still_applies_otherwise(self):
        policy.handle_event(self.db, prompt(), now=T0)
        self.db.set_settings({"min_elapsed_s": 0})
        self.assertEqual(policy.handle_event(self.db, stop(stop_hook_active=True), now=T0 + 300), "skip:stop_hook_loop")

    def test_project_switch_off_still_wins(self):
        pd = self.db.one("SELECT project_dir FROM sessions WHERE session_id='s1'")["project_dir"]
        self.db.set_project_enabled(pd, "proj", False, T0)
        self.queue_reply()
        policy.handle_event(self.db, prompt(), now=T0 + 2)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 20), "skip:project_off")


if __name__ == "__main__":
    unittest.main()
