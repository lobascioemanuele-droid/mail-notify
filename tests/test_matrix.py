"""Combination tests: every mail kind x content mode x language x reply-channel options, and the policy gates
against `simulate` (the dry run shown in the web app), which must always agree with what really happens."""
import itertools
import re
import unittest

from ccmail import policy, render
from helpers import TmpHome

SECRET = "ghp_" + "a" * 36
MSG = ("Ho completato la migrazione dei dati. Sono stati elaborati 1200 record e 12 test su 12 passano. "
       "Il file report.md contiene i dettagli. Token usato: " + SECRET + ". Vuoi che apra la PR?")
MODES = ["summary", "full", "title", "minimal"]
LANGS = ["it", "en"]

# (label, event, payload extras, render kwargs)
KINDS = [
    ("stop", "Stop", {"last_assistant_message": MSG}, {}),
    ("stop_bg_done", "Stop", {"last_assistant_message": MSG}, {"kind": "bg_done"}),
    ("stop_bg_status", "Stop", {"last_assistant_message": MSG, "background_tasks": [{"id": "t1"}, {"id": "t2"}]},
     {"kind": "bg_status", "n_bg": 2}),
    ("stop_limit", "Stop", {"last_assistant_message": "You've hit your session limit. Resets 5pm (Europe/Rome)"},
     {"limit_hit": True}),
    ("notify_permission", "Notification", {"message": "Claude needs your permission to use Bash",
                                           "notification_type": "permission_prompt", "title": "Permission"}, {}),
    ("notify_input", "Notification", {"message": "Claude is waiting for your input",
                                      "notification_type": "agent_needs_input"}, {}),
    ("failure", "StopFailure", {"error": "rate_limit", "last_assistant_message": MSG}, {}),
    ("session_end", "SessionEnd", {}, {}),
]
ITALIAN = ("Progetto:", "ESITO", "COMANDI SUGGERITI", "Durata turno", "Rispondi a questa", "Processi in background",
           "GESTIONE DA QUESTA", "Ancora attivi")
ENGLISH = ("Project:", "OUTCOME", "SUGGESTED COMMANDS", "Turn duration", "Reply to this e-mail", "Background processes",
           "MANAGE FROM THIS", "Still running")


class RenderMatrix(TmpHome):
    def build(self, kind, mode, lang, inbound, to_claude, sugg, device=""):
        _, event, extra, kw = kind
        s = self.db.get_settings()
        s.update(content_mode=mode, language=lang, inbound_enabled=inbound, inbound_to_claude=to_claude,
                 suggestions_enabled=sugg, device_name=device)
        payload = {"hook_event_name": event, "session_id": "abcdef1234567890", **extra}
        return render.build(event, payload, s, "proj", 400 if event == "Stop" else None, chat_title="Chat di prova",
                            ref="ccm-0123456789ab" if inbound else None, inbound=inbound, to_claude=to_claude, **kw)

    def test_every_combination(self):
        n = 0
        for kind, mode, lang, inbound, to_claude, sugg in itertools.product(
                KINDS, MODES, LANGS, (False, True), (False, True), (False, True)):
            with self.subTest(kind=kind[0], mode=mode, lang=lang, inbound=inbound, to_claude=to_claude, sugg=sugg):
                subject, body = self.build(kind, mode, lang, inbound, to_claude, sugg)
                n += 1
                # shape
                self.assertTrue(subject.startswith("[Claude Code] proj"))
                self.assertLessEqual(len(subject), 150)
                self.assertNotRegex(subject, r"[\x00-\x1f]")
                self.assertTrue(body.endswith("\n"))
                self.assertIn("Chat: Chat di prova", body)
                self.assertIn("Account:", body)
                # secrets never leave, in any mode
                self.assertNotIn(SECRET, subject + body)
                # reply-channel instructions only when the channel is on
                self.assertEqual("Rif: ccm-0123456789ab" in body, inbound)
                if inbound:
                    self.assertEqual(("delivered to Claude" in body) or ("consegnato a Claude" in body), to_claude)
                # suggestions follow the switch (never on SessionEnd)
                has_sug = ("COMANDI SUGGERITI" in body) or ("SUGGESTED COMMANDS" in body)
                if not sugg or kind[1] == "SessionEnd":
                    self.assertFalse(has_sug)
                # model text follows the content mode
                if mode == "minimal":
                    self.assertNotIn("migrazione", subject + body)
                if mode == "title" and kind[1] == "Stop" and not kind[3].get("limit_hit"):
                    self.assertNotIn("1200 record", body)
                # language is consistent: no labels of the other language
                other = ENGLISH if lang == "it" else ITALIAN
                for w in other:
                    self.assertNotIn(w, body, f"{w!r} leaked into {lang} mail")
        self.assertEqual(n, len(KINDS) * len(MODES) * len(LANGS) * 8)

    def test_summary_is_bounded_and_whole_sentences(self):
        for lang in LANGS:
            _, body = self.build(KINDS[0], "summary", lang, False, False, False)
            esito = body.split("\n")
            text = " ".join(esito[esito.index(next(x for x in esito if x in ("ESITO", "OUTCOME"))) + 1:]).strip()
            self.assertLessEqual(len(text), 700)
            self.assertTrue(re.search(r"[.!?»)]$", text) or text.endswith("]"), text[-30:])

    def test_device_line_both_languages(self):
        for lang, label in (("it", "Dispositivo: PC-Casa"), ("en", "Device: PC-Casa")):
            _, body = self.build(KINDS[0], "summary", lang, False, False, False, device="PC-Casa")
            self.assertIn(label, body)

    def test_usage_limit_names_reset_time(self):
        subject, _ = self.build(KINDS[3], "minimal", "en", False, False, False)
        self.assertIn("usage limit reached", subject)
        self.assertIn("5pm", subject)


class PolicyMatrix(TmpHome):
    """handle_event (real) and simulate (dry run) must agree for every combination of gates."""

    def run_real(self, sid, event, ntype, elapsed, project_on, enabled, snooze, events_on, types):
        s = {"enabled": enabled, "events_enabled": {"Stop": events_on, "Notification": events_on,
                                                    "StopFailure": events_on, "UsageLimit": True, "SessionEnd": False},
             "notify_types": types, "snooze_until": 9_999_999_999 if snooze else 0, "min_elapsed_s": 120,
             "toggled_at": 0}
        s["max_per_hour"] = 100000  # the hourly cap is shared across sessions: keep it out of this matrix
        self.db.set_settings(s)
        base = {"session_id": sid, "cwd": "C:/x/p-" + sid}
        T = 5_000_000.0
        policy.handle_event(self.db, {**base, "hook_event_name": "UserPromptSubmit"}, now=T)
        pd = self.db.one("SELECT project_dir FROM sessions WHERE session_id=?", (sid,))["project_dir"]
        self.db.set_project_enabled(pd, "p", project_on, T)
        payload = {**base, "hook_event_name": event, "notification_type": ntype, "last_assistant_message": "ok",
                   "message": "ok"}
        at = T + (elapsed if elapsed is not None else 1)
        # dry run first: the real event updates the session (cooldown), which the dry run would then see
        sim = policy.simulate(self.db, event, elapsed_s=elapsed, message="ok", ntype=ntype, session_id=sid, now=at)
        decision = policy.handle_event(self.db, payload, now=at)
        return decision, sim

    def test_gate_combinations_agree_with_simulate(self):
        n = 0
        combos = itertools.product(("Stop", "Notification"), ("", "permission_prompt", "idle_prompt"),
                                   (10, 119, 120, 600), (True, False), (True, False), (False, True), (True, False))
        for i, (event, ntype, elapsed, project_on, enabled, snooze, events_on) in enumerate(combos):
            if event == "Stop" and ntype:
                continue
            if event == "Notification" and not ntype:
                continue
            with self.subTest(event=event, ntype=ntype, elapsed=elapsed, project_on=project_on, enabled=enabled,
                              snooze=snooze, events_on=events_on):
                decision, sim = self.run_real(f"s{i}", event, ntype, elapsed if event == "Stop" else None,
                                              project_on, enabled, snooze, events_on,
                                              ["permission_prompt", "agent_needs_input"])
                n += 1
                sent = decision.startswith("queued")
                self.assertEqual(sent, sim["would_send"], (decision, sim["reason"]))
                if not sent:
                    self.assertEqual(decision.split(":", 1)[1], sim["reason"])
        self.assertGreater(n, 100)


if __name__ == "__main__":
    unittest.main()
