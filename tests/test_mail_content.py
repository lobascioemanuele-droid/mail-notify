import json
import os
import unittest
from unittest import mock

from ccmail import config, policy
from ccmail.worker import Worker
from helpers import TmpHome

T0 = 2_000_000.0


class HeaderTests(TmpHome):
    def mail(self, **settings):
        self.enable(T0 - 10000)
        self.db.set_settings(settings)
        policy.handle_event(self.db, {"hook_event_name": "UserPromptSubmit", "session_id": "abcdef123456",
                                      "cwd": "C:/w/demo"}, now=T0)
        policy.handle_event(self.db, {"hook_event_name": "Stop", "session_id": "abcdef123456", "cwd": "C:/w/demo",
                                      "last_assistant_message": "Fatto."}, now=T0 + 400)
        return self.db.q("SELECT subject, body FROM outbox")[0]

    def test_account_replaces_host(self):
        m = self.mail(account_name="Mario Rossi")
        self.assertIn("Progetto: demo", m["body"])
        self.assertIn("Account: Mario Rossi", m["body"])
        self.assertIn("Sessione: abcdef12\n", m["body"])
        self.assertNotIn("Host:", m["body"])

    def test_account_falls_back_to_windows_user(self):
        with mock.patch.dict(os.environ, {"USERNAME": "Lobas"}):
            self.assertIn("Account: Lobas", self.mail()["body"])

    def test_account_defaults_to_the_claude_account_name(self):
        with mock.patch("ccmail.identity.claude_display_name", return_value="Emanuele"),                 mock.patch.dict(os.environ, {"USERNAME": "Lobas"}):
            self.assertIn("Account: Emanuele", self.mail()["body"])

    def test_configured_name_beats_claude_account_name(self):
        with mock.patch("ccmail.identity.claude_display_name", return_value="Emanuele"):
            self.assertIn("Account: Mario Rossi", self.mail(account_name="Mario Rossi")["body"])

    def test_account_name_validation(self):
        self.assertEqual(config.validate({"account_name": "  Ada Lovelace "}), {"account_name": "Ada Lovelace"})
        for bad in ("x" * 61, "a\r\nBcc: z@z.z", 5):
            with self.assertRaises(ValueError):
                config.validate({"account_name": bad})


class WatchdogMailTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.enable(0)
        self.db.set_settings({"watchdog_minutes": 15, "heartbeat_minutes": 5, "account_name": "Mario Rossi"})
        for sid, proj in (("s-uscite-0001", "uscite"), ("s-oropan-0002", "oropan - Copia")):
            self.db.touch_session(sid, f"C:/w/{proj}", T0)
            self.db.set_session(sid, state="idle", bg_tasks=1, bg_ids=json.dumps(["b1"]))
        self.w = Worker(self.db)

    def test_bodies_identify_project_account_session(self):
        self.w.watchdog(now=T0 + 20 * 60)
        rows = self.db.q("SELECT subject, body FROM outbox ORDER BY id")
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["body"], rows[1]["body"])  # was: two identical-looking mails
        for r, proj, sid in zip(rows, ("uscite", "oropan - Copia"), ("s-uscite-0001", "s-oropan-0002")):
            self.assertIn(f"Progetto: {proj}", r["body"])
            self.assertIn("Account: Mario Rossi", r["body"])
            self.assertIn(f"Sessione: {sid[:8]}\n", r["body"])
            self.assertIn("Nessun evento da 15 min", r["body"])
            self.assertIn("b1", r["body"])

    def test_not_sent_if_a_mail_for_the_session_went_out_recently(self):
        self.db.set_session("s-uscite-0001", last_sent_at=T0 + 10 * 60)
        self.w.watchdog(now=T0 + 20 * 60)
        subjects = [r["subject"] for r in self.db.q("SELECT subject FROM outbox")]
        self.assertEqual(len(subjects), 1)
        self.assertIn("oropan", subjects[0])

    def test_paused_when_it_would_fire_before_a_heartbeat(self):
        self.db.set_settings({"heartbeat_minutes": 30})  # 15 < 2*30: invalid combination
        self.w.watchdog(now=T0 + 20 * 60)
        self.assertEqual(self.db.q("SELECT * FROM outbox"), [])
        self.db.set_settings({"heartbeat_mode": "off"})  # no heartbeat: the watchdog is the only net
        self.w._last_watchdog = 0
        self.w.watchdog(now=T0 + 20 * 60)
        self.assertEqual(len(self.db.q("SELECT * FROM outbox")), 2)

    def test_respects_snooze(self):
        self.db.set_settings({"snooze_until": T0 + 3600 * 5})
        self.w.watchdog(now=T0 + 20 * 60)
        self.assertEqual(self.db.q("SELECT * FROM outbox"), [])


if __name__ == "__main__":
    unittest.main()


class TestMailRecordedTests(TmpHome):
    def test_test_mail_is_recorded_and_excluded_from_cap(self):
        from ccmail import mailer
        with mock.patch.object(mailer, "send", return_value=None):
            mailer.send_test(self.db, self.db.get_settings())
        row = self.db.one("SELECT status, kind, sent_at FROM outbox")
        self.assertEqual((row["status"], row["kind"]), ("sent", "test"))
        self.assertIsNotNone(row["sent_at"])
        self.assertEqual(self.db.sent_last_hour(row["sent_at"] + 1), 0)

    def test_failed_test_is_recorded_then_cleared_by_a_success(self):
        from ccmail import mailer
        with mock.patch.object(mailer, "send", side_effect=mailer.MailError("x")):
            with self.assertRaises(mailer.MailError):
                mailer.send_test(self.db, self.db.get_settings())
        self.assertEqual(self.db.one("SELECT status FROM outbox")["status"], "failed")
        import time as _t
        _t.sleep(0.01)
        with mock.patch.object(mailer, "send", return_value=None):
            mailer.send_test(self.db, self.db.get_settings())
        from ccmail.server import overview_dict
        self.assertEqual(overview_dict(self.db)["last_24h"]["failed"], 0)
        self.assertEqual(overview_dict(self.db)["last_mail"]["subject"], "[Claude Code] Email di prova")


class NewMailFormatTests(TmpHome):
    def send(self, event, payload, **settings):
        self.enable(T0 - 9999)
        self.db.set_settings({"mute_after_toggle_s": 0, **settings})
        policy.handle_event(self.db, {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "C:/w/demo"},
                            now=T0)
        policy.handle_event(self.db, {"hook_event_name": event, "session_id": "s1", "cwd": "C:/w/demo", **payload},
                            now=T0 + 400)
        return self.db.q("SELECT subject, body FROM outbox ORDER BY id DESC")[0]

    def test_sections_and_suggestions(self):
        m = self.send("Stop", {"last_assistant_message": "Fatto il refactoring. Vuoi che apra una PR?"})
        for title in ("ESITO", "COMANDI SUGGERITI"):
            self.assertIn(title, m["body"])
        # the message ends with a question: the suggestion quotes it instead of a generic "yes"
        self.assertIn("1. Sì, procedi con quanto proposto: «Vuoi che apra una PR?»", m["body"])
        self.assertIn("─" * 30, m["body"])

    def test_suggestions_can_be_turned_off(self):
        m = self.send("Stop", {"last_assistant_message": "Fatto."}, suggestions_enabled=False)
        self.assertNotIn("COMANDI SUGGERITI", m["body"])

    def test_usage_limit_is_its_own_event_and_not_cut(self):
        txt = "You've hit your session limit · resets 7:20pm (Europe/Rome)"
        m = self.send("StopFailure", {"error": "rate_limit", "last_assistant_message": txt})
        self.assertIn("limite di utilizzo raggiunto (si resetta 7:20pm (Europe/Rome))", m["subject"])
        self.assertIn(txt, m["body"])  # whole text, no ellipsis
        self.assertNotIn("…", m["body"])

    def test_usage_limit_flag_off_suppresses_it(self):
        self.enable(T0 - 9999)
        self.db.set_settings({"events_enabled": {"UsageLimit": False}})
        r = policy.handle_event(self.db, {"hook_event_name": "StopFailure", "session_id": "s1", "cwd": "C:/w/d",
                                          "error": "rate_limit", "last_assistant_message": "limit"}, now=T0)
        self.assertEqual(r, "skip:event_off")

    def test_usage_limit_bypasses_cooldown_and_background_silence(self):
        self.enable(T0 - 9999)
        self.db.set_settings({"mute_after_toggle_s": 0})
        policy.handle_event(self.db, {"hook_event_name": "Notification", "session_id": "s1", "cwd": "C:/w/d",
                                      "notification_type": "permission_prompt"}, now=T0)
        r = policy.handle_event(self.db, {"hook_event_name": "Stop", "session_id": "s1", "cwd": "C:/w/d",
                                          "background_tasks": [{"id": "b"}],
                                          "last_assistant_message": "Il limite di utilizzo è stato raggiunto"},
                                now=T0 + 5)
        self.assertEqual(r, "queued:limit")

    def test_subject_is_never_a_cut_fragment(self):
        long = "Questa è una frase molto lunga che supera di parecchio il limite previsto per l'oggetto della mail " \
               "e quindi non deve essere troncata a metà parola"
        m = self.send("Stop", {"last_assistant_message": long}, content_mode="title")
        self.assertNotIn("…", m["subject"])
        self.assertTrue(m["subject"].endswith("task completato"))

    def test_summary_keeps_whole_sentences(self):
        item = "Equivalenza con il lab: matrice identica (15.240.326 righe, 23 variabili). " + "Parola " * 60 + "fine."
        m = self.send("Stop", {"last_assistant_message": "Esito.\n\n- " + item + "\n- Altro punto breve."})
        self.assertIn("Equivalenza con il lab: matrice identica (15.240.326 righe, 23 variabili).", m["body"])
        self.assertNotIn("Parola Parola", m["body"].split("COMANDI")[0])  # long tail dropped, not cut mid-sentence

    def test_manage_section_only_when_inbound_is_on(self):
        self.assertNotIn("Rif: ccm-", self.send("Stop", {"last_assistant_message": "Fatto."})["body"])
        self.db.x("DELETE FROM outbox")
        self.db.set_session("s1", last_sent_at=None)  # same session again: clear the cooldown
        m = self.send("Stop", {"last_assistant_message": "Fatto."}, inbound_enabled=True)
        self.assertIn("GESTIONE DA QUESTA EMAIL", m["body"])
        ref = m["body"].split("Rif: ")[1].split()[0]
        self.assertEqual(self.db.lookup_ref(ref, T0 + 500), "c:/w/demo")
