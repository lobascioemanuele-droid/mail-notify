import unittest

from ccmail import inbound


class WrappedQuoteTests(unittest.TestCase):
    def test_wrapped_quote_intro_is_cut(self):
        text = ("Mostrami il diff delle modifiche.\n\nIl dom 11 ott 2026, 02:17 Claude Code <a@b.c>\n"
                "ha scritto:\n> Progetto")
        self.assertEqual(inbound.reply_body(text), "Mostrami il diff delle modifiche.")

    def test_english_and_plain_sentences(self):
        self.assertEqual(inbound.reply_body("Ok\nOn Sun, 11 Oct 2026 at 02:17, X wrote:\n> q"), "Ok")
        self.assertEqual(inbound.reply_body("Il progetto parte il 11 ottobre"), "Il progetto parte il 11 ottobre")

    def test_quote_only_gives_nothing(self):
        self.assertEqual(inbound.reply_body("> Progetto: demo\n> Rif: ccm-0123456789ab"), "")


if __name__ == "__main__":
    unittest.main()
