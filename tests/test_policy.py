import unittest

from ccmail import policy
from helpers import TmpHome

T0 = 1_000_000.0


def stop(sid="s1", msg="Fatto.", **kw):
    return {"hook_event_name": "Stop", "session_id": sid, "cwd": "C:/x/proj", "last_assistant_message": msg, **kw}


def prompt(sid="s1"):
    return {"hook_event_name": "UserPromptSubmit", "session_id": sid, "cwd": "C:/x/proj"}


class PolicyTests(TmpHome):
    def queued(self):
        return self.db.q("SELECT * FROM outbox")

    def test_disabled_sends_nothing(self):
        policy.handle_event(self.db, prompt(), now=T0)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 600), "skip:disabled")

    def test_short_turn_skipped_long_turn_sent(self):
        self.enable(T0 - 1000)
        policy.handle_event(self.db, prompt(), now=T0)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 30), "skip:short_turn")
        policy.handle_event(self.db, prompt(), now=T0 + 100)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 400), "queued")
        self.assertEqual(len(self.queued()), 1)
        self.assertIn("proj", self.queued()[0]["subject"])

    def test_no_confirmation_mail_after_toggle(self):
        # the exact complaint: the turn that turns notifications on must not mail
        self.enable(T0)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 20), "skip:short_turn")
        policy.handle_event(self.db, prompt(), now=T0 + 25)
        self.db.set_settings({"min_elapsed_s": 0})
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 30), "skip:muted_after_toggle")
        self.assertEqual(self.queued(), [])

    def test_cooldown_per_session(self):
        self.enable(T0 - 1000)
        policy.handle_event(self.db, prompt(), now=T0)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 200), "queued")
        policy.handle_event(self.db, prompt(), now=T0 + 210)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 250), "skip:short_turn")
        n = {"hook_event_name": "Notification", "session_id": "s1", "notification_type": "permission_prompt", "message": "ok?"}
        self.assertEqual(policy.handle_event(self.db, n, now=T0 + 230), "skip:cooldown")
        self.assertEqual(policy.handle_event(self.db, {**n, "session_id": "s2"}, now=T0 + 230), "queued")

    def test_notification_type_filter(self):
        self.enable(T0 - 1000)
        n = {"hook_event_name": "Notification", "session_id": "s1", "notification_type": "idle_prompt"}
        self.assertEqual(policy.handle_event(self.db, n, now=T0), "skip:type_off")

    def test_hourly_cap(self):
        self.enable(T0 - 1000)
        self.db.set_settings({"max_per_hour": 2, "cooldown_s": 0})
        n = lambda i: {"hook_event_name": "Notification", "session_id": f"s{i}", "notification_type": "permission_prompt"}
        res = [policy.handle_event(self.db, n(i), now=T0 + i) for i in range(4)]
        self.assertEqual(res, ["queued", "queued", "skip:hourly_cap", "skip:hourly_cap"])

    def test_stale_spooled_event_not_mailed(self):
        self.enable(T0 - 1000)
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0 + 5000, received_at=T0), "skip:stale")

    def test_secrets_redacted_in_body(self):
        self.enable(T0 - 1000)
        policy.handle_event(self.db, prompt(), now=T0)
        policy.handle_event(self.db, stop(msg="Fatto. password=hunter2 e ghp_" + "a" * 36), now=T0 + 500)
        row = self.queued()[0]
        self.assertNotIn("hunter2", row["body"])
        self.assertNotIn("ghp_" + "a" * 10, row["body"])

    def test_minimal_mode_has_no_model_text_anywhere(self):
        self.enable(T0 - 1000)
        self.db.set_settings({"content_mode": "minimal"})
        policy.handle_event(self.db, prompt(), now=T0)
        policy.handle_event(self.db, stop(msg="Dettaglio riservato Acme"), now=T0 + 500)
        row = self.queued()[0]
        self.assertEqual(row["subject"], "[Claude Code] proj: task completato")
        self.assertNotIn("riservato", row["subject"] + row["body"])
        n = {"hook_event_name": "Notification", "session_id": "s9", "notification_type": "permission_prompt",
             "title": "Run rm -rf secret-dir?", "message": "Allow Bash: rm -rf secret-dir"}
        policy.handle_event(self.db, n, now=T0 + 900)
        last = self.queued()[-1]
        self.assertNotIn("secret-dir", last["subject"] + last["body"])

    def test_title_mode_has_no_message_text(self):
        self.enable(T0 - 1000)
        self.db.set_settings({"content_mode": "title"})
        policy.handle_event(self.db, prompt(), now=T0)
        policy.handle_event(self.db, stop(msg="titolo\nsegreto nel corpo"), now=T0 + 500)
        self.assertNotIn("segreto", self.queued()[0]["body"])


TASKS = [{"id": "b1", "type": "shell", "status": "running"}]


class BackgroundTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.enable(T0 - 10000)

    def queued(self):
        return self.db.q("SELECT subject, body FROM outbox ORDER BY id")

    def long_turn(self, t, **stop_kw):
        policy.handle_event(self.db, prompt(), now=t)
        return policy.handle_event(self.db, stop(**stop_kw), now=t + 300)

    def test_unknown_duration_is_treated_as_short(self):
        self.assertEqual(policy.handle_event(self.db, stop(), now=T0), "skip:short_turn")

    def test_stop_with_running_tasks_is_silent(self):
        self.assertEqual(self.long_turn(T0, background_tasks=TASKS), "skip:bg_running")
        self.assertEqual(self.queued(), [])

    def test_all_tasks_done_sends_one_mail(self):
        self.long_turn(T0, background_tasks=TASKS)
        self.assertEqual(self.long_turn(T0 + 1000, background_tasks=[]), "queued:bg_done")
        self.assertIn("terminati", self.queued()[0]["subject"])
        # a following ordinary short turn does not mail again
        self.assertEqual(self.long_turn(T0 + 2000, background_tasks=[]), "queued")  # long ordinary turn only

    def test_done_mail_ignores_short_turn_filter(self):
        policy.handle_event(self.db, stop(background_tasks=TASKS), now=T0)  # no prompt: unknown duration
        self.assertEqual(policy.handle_event(self.db, stop(background_tasks=[]), now=T0 + 5), "queued:bg_done")

    def test_pending_heartbeat_turns_next_stop_into_status_mail(self):
        policy.handle_event(self.db, stop(background_tasks=TASKS), now=T0)
        self.db.set_session("s1", heartbeat_pending=1)
        self.assertEqual(policy.handle_event(self.db, stop(msg="Task al 40%", background_tasks=TASKS),
                                             now=T0 + 1800), "queued:bg_status")
        m = self.queued()[0]
        self.assertIn("stato processi in background (1 attivi)", m["subject"])
        self.assertIn("40%", m["body"])
        self.assertEqual(self.db.one("SELECT heartbeat_pending p FROM sessions")["p"], 0)

    def test_wakeup_prompt_event_does_not_clear_pending(self):
        policy.handle_event(self.db, stop(background_tasks=TASKS), now=T0)
        self.db.set_session("s1", heartbeat_pending=1)
        policy.handle_event(self.db, prompt(), now=T0 + 10)
        self.assertEqual(policy.handle_event(self.db, stop(background_tasks=TASKS), now=T0 + 20), "queued:bg_status")

    def test_skill_mode_self_scheduled_status(self):
        self.db.set_settings({"heartbeat_mode": "skill", "heartbeat_minutes": 20})
        self.assertEqual(policy.handle_event(self.db, stop(background_tasks=TASKS), now=T0), "queued:bg_status")
        # too soon after the previous status mail: silent
        self.assertEqual(policy.handle_event(self.db, stop(background_tasks=TASKS), now=T0 + 600), "skip:bg_running")
        self.assertEqual(policy.handle_event(self.db, stop(background_tasks=TASKS), now=T0 + 1300), "queued:bg_status")

    def test_notify_stop_with_bg_option(self):
        self.db.set_settings({"notify_stop_with_bg": True})
        self.assertEqual(self.long_turn(T0, background_tasks=TASKS), "queued")

    def test_background_start_is_recorded(self):
        ev = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Bash",
              "tool_input": {"command": "sleep 40", "run_in_background": True},
              "tool_response": {"backgroundTaskId": "bx1"}}
        self.assertEqual(policy.handle_event(self.db, ev, now=T0), "recorded")
        self.assertIn("bx1", self.db.q("SELECT summary FROM events")[0]["summary"])
        plain = {**ev, "tool_input": {"command": "ls"}, "tool_response": {"stdout": ""}}
        self.assertEqual(policy.handle_event(self.db, plain, now=T0 + 1), "ignored")
        self.assertEqual(len(self.db.q("SELECT * FROM events")), 1)  # plain Bash calls leave no rows


if __name__ == "__main__":
    unittest.main()
