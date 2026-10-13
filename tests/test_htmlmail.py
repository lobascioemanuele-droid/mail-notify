"""HTML alternative: reply buttons, scrollable full message, plain part untouched."""
import email
import re
import unittest
from urllib.parse import parse_qs, unquote, urlparse

from ccmail import htmlmail, inbound, mailer, policy, render
from helpers import TmpHome

REF = "ccm-0123456789ab"
LONG = "Ho analizzato i dati. " + "Riga di dettaglio numero {i}. " * 1 + "Fine."
FULL = " ".join(f"Frase {i} del risultato." for i in range(120))


class HtmlMailTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.s = self.db.get_settings()
        self.s.update(language="it", inbound_enabled=True, inbound_to_claude=True, content_mode="summary")
        self.subject, self.body = render.build(
            "Stop", {"hook_event_name": "Stop", "session_id": "abcdef1234", "last_assistant_message": FULL},
            self.s, "proj", 400, chat_title="Chat prova", ref=REF, inbound=True, to_claude=True)

    @staticmethod
    def bodies(h):
        return [parse_qs(urlparse(l.replace("&amp;", "&")).query)["body"][0]
                for l in re.findall(r'href="(mailto:[^"]+)"', h)]

    def test_buttons_are_mailto_with_command_and_ref(self):
        h = htmlmail.render(self.subject, self.body, self.s, "me@example.com", FULL)
        bodies = self.bodies(h)
        for l in re.findall(r'href="(mailto:[^"]+)"', h):
            u = urlparse(l.replace("&amp;", "&"))
            self.assertEqual(unquote(u.path), "me@example.com")
            q = parse_qs(u.query)
            self.assertTrue(q["subject"][0].startswith("Re: [Claude Code]"))
            self.assertIn("Rif: " + REF, q["body"][0])
        cmds = [b.splitlines()[0] for b in bodies]
        for c in ("OFF", "ON", "PAUSA 60", "PAUSA 15", "PAUSA 180", "PAUSA 480"):
            self.assertIn(c, cmds)

    def test_button_text_is_a_command_the_inbound_parser_accepts(self):
        h = htmlmail.render(self.subject, self.body, self.s, "me@example.com", FULL)
        for b in self.bodies(h):
            first = b.splitlines()[0]
            if first.split(" ")[0] in ("OFF", "ON", "PAUSA"):
                self.assertIsNotNone(inbound.parse_command(b))
            self.assertIn(REF, inbound.REF_RE.findall(b))

    def test_suggested_commands_are_tappable_and_aligned(self):
        h = htmlmail.render(self.subject, self.body, self.s, "me@example.com", FULL)
        texts = [ln.strip().split(". ", 1)[1] for ln in self.body.splitlines()
                 if re.match(r"^\s+\d+\. ", ln)]
        self.assertTrue(texts)
        sent = [b.splitlines()[0] for b in self.bodies(h)]
        for t in texts:
            self.assertIn(t, sent)  # each one is a link whose reply text is the command itself
        self.assertIn("border:1px solid #000000", h)
        self.assertIn('width="26"', h)  # number column: wrapped text stays under the text, not under the number

    def test_suggested_commands_not_links_when_delivery_to_claude_is_off(self):
        s = {**self.s, "inbound_to_claude": False}
        h = htmlmail.render(self.subject, self.body, s, "me@example.com", FULL)
        self.assertNotIn("border:1px solid #000000", h)
        self.assertNotIn("Scrivi a Claude", h)

    def test_cards_share_width_and_height(self):
        h = htmlmail.render(self.subject, self.body, self.s, "me@example.com", FULL)
        self.assertIn("table-layout:fixed", h)
        self.assertEqual(h.count('<td width="33%"'), 6)  # three action cards + three pause cells

    def test_scroll_box_repeats_nothing_already_shown_above(self):
        shown = "Ho verificato i dati. Tutto coerente."
        rest = htmlmail.remainder("Ho verificato i dati. Dettaglio uno. Dettaglio due. Tutto coerente.", shown)
        self.assertNotIn("Ho verificato i dati", rest)
        self.assertNotIn("Tutto coerente", rest)
        self.assertIn("Dettaglio uno. Dettaglio due.", rest)
        self.assertEqual(htmlmail.remainder("Solo questo.", "Solo questo. [… messaggio completo: 12 caratteri]"), "")

    def test_box_label_has_no_scroll_hint_and_pauses_share_one_row(self):
        h = htmlmail.render(self.subject, self.body, self.s, "me@example.com", FULL)
        self.assertNotIn("scorri dentro", h)
        self.assertEqual(h.count('<td width="33%"'), 6)  # 3 action cards + 3 pause cells
        self.assertNotIn("calc(100%", h)  # no negative margins that push the block off centre
        self.assertIn("text-align:center", h)

    def test_full_message_in_scroll_box_only_when_longer(self):
        h = htmlmail.render(self.subject, self.body, self.s, "me@example.com", FULL)
        self.assertIn("overflow-y:auto", h)
        self.assertIn("Frase 119 del risultato.", h)
        short = htmlmail.render(self.subject, self.body, self.s, "me@example.com", "")
        self.assertNotIn("overflow-y:auto", short)

    def test_instructions_replaced_and_text_escaped(self):
        h = htmlmail.render("<b>x</b>", self.body + "\n<script>alert(1)</script>", self.s, "me@example.com", "<i>&</i>" * 80)
        self.assertNotIn("<script>", h)
        self.assertNotIn("<b>x</b>", h)
        self.assertNotIn("Rispondi a questa email scrivendo", h)

    def test_no_buttons_without_reference(self):
        subject, body = render.build("Stop", {"last_assistant_message": "ok"}, self.s, "p", 400)
        h = htmlmail.render(subject, body, self.s, "me@example.com")
        self.assertNotIn("mailto:", h)

    def test_message_keeps_plain_part_and_adds_html(self):
        m = mailer.build_message("a@x", "a@x", self.subject, self.body, "<p>x</p>")
        self.assertEqual(m.get_body(("plain",)).get_content(), self.body)
        self.assertIn("<p>x</p>", m.get_body(("html",)).get_content())
        self.assertFalse(mailer.build_message("a@x", "a@x", self.subject, self.body).is_multipart())

    def test_full_text_follows_content_mode_and_redacts(self):
        secret = "ghp_" + "a" * 36
        p = {"last_assistant_message": "Fatto " + secret}
        self.assertIn("Fatto", render.full_text("Stop", p, {"content_mode": "full"}))
        self.assertNotIn(secret, render.full_text("Stop", p, {"content_mode": "summary"}))
        self.assertEqual(render.full_text("Stop", p, {"content_mode": "title"}), "")
        self.assertEqual(render.full_text("Stop", p, {"content_mode": "minimal"}), "")

    def test_policy_stores_full_text_only_when_enabled(self):
        self.enable(1_000.0)
        for flag in (False, True):
            self.db.set_settings({"html_mail": flag, "min_elapsed_s": 0, "cooldown_s": 0, "max_per_hour": 1000})
            policy.handle_event(self.db, {"hook_event_name": "UserPromptSubmit", "session_id": "h", "cwd": "C:/x/p"},
                                now=2_000.0 + flag * 1000)
            policy.handle_event(self.db, {"hook_event_name": "Stop", "session_id": "h", "cwd": "C:/x/p",
                                          "last_assistant_message": FULL}, now=2_500.0 + flag * 1000)
        rows = self.db.q("SELECT full_text FROM outbox ORDER BY id")
        self.assertFalse(rows[0]["full_text"])
        self.assertIn("Frase 119", rows[1]["full_text"])


if __name__ == "__main__":
    unittest.main()


class ConfirmationMailTests(TmpHome):
    """The mails ccmail sends about a reply get the same HTML layout as the notifications."""

    def setUp(self):
        super().setUp()
        self.s = {**self.db.get_settings(), "language": "it", "inbound_to_claude": True}

    def html(self, subject, body):
        return htmlmail.render(subject, body, self.s, "me@example.com")

    def test_queued_confirmation(self):
        from ccmail import ack
        subject, body = ack.build_claude_ack(self.db, self.s, "s1", "Controlla i risultati", 1_000.0,
                                             project="demo", chat_title="Chat", ref=REF)
        h = self.html(subject, body)
        self.assertIn("RICEVUTA, IN CODA", h)
        self.assertIn("Controlla i risultati", h)
        self.assertIn("border-left:4px solid", h)  # the quoted reply is a quote block
        self.assertIn("mailto:", h)
        self.assertNotIn("Rispondi a questa email scrivendo", h)

    def test_command_executed(self):
        from ccmail import ack
        subject, body = ack.build_command_ack(self.s, "off", "demo", "Le notifiche del progetto «demo» sono state disattivate.", ref=REF)
        h = self.html(subject, body)
        self.assertIn("COMANDO ESEGUITO", h)
        self.assertIn("disattivate", h)

    def test_discarded_reply(self):
        from ccmail import ack
        subject, body = ack.build_rejection(self.s, "no_session", "Ok perfetto", "demo", ref=REF)
        h = self.html(subject, body)
        self.assertIn("RISPOSTA SCARTATA", h)
        self.assertIn("Ok perfetto", h)
        self.assertIn("COSA FARE", h)

    def test_english_badges(self):
        from ccmail import ack
        s = {**self.s, "language": "en"}
        subject, body = ack.build_rejection(s, "no_ref", "hi", "demo", ref=REF)
        self.assertIn("REPLY DISCARDED", htmlmail.render(subject, body, s, "me@example.com"))
