import unittest

from ccmail import suggest as sg


class PersonalisedSuggestionTests(unittest.TestCase):
    def test_question_is_quoted_in_the_yes_suggestion(self):
        s = sg.suggest("Stop", None, "Ho finito il refactoring del modulo.\n\nVuoi che apra una PR?")
        self.assertTrue(s[0].startswith("Sì, procedi con quanto proposto"))
        self.assertIn("Vuoi che apra una PR?", s[0])
        self.assertTrue(any(x.startswith("No, fermati") for x in s))

    def test_files_and_tests_come_from_the_message(self):
        msg = "Ho modificato acme/modelli/fattore_kg.py e aggiornato i fixture. 39 test su 39 passati."
        s = sg.suggest("Stop", None, msg)
        joined = "\n".join(s)
        self.assertIn("fattore_kg.py", joined)
        self.assertIn("39", joined)  # the number of tests is reused

    def test_error_line_is_reused(self):
        msg = "Il fit è fallito: errore di convergenza al fold 2.\nRiprovo con un altro seed."
        s = sg.suggest("Stop", None, msg)
        self.assertTrue(any("errore di convergenza" in x for x in s))

    def test_background_mails_are_specific(self):
        done = sg.suggest("Stop", "bg_done", "Il fit è finito. Vedi equiv_A.log: matrice identica.")
        self.assertTrue(any("equiv_A.log" in x for x in done))
        st = sg.suggest("Stop", "bg_status", "Sono al 40% del fit.")
        self.assertIn("40%", st[0])
        fold = sg.suggest("Stop", "bg_status", "Sono al fold 2 di 3 dei nuovi alfa.")
        self.assertIn("fold 2 di 3", fold[0])

    def test_permission_prompt_mentions_what_is_requested(self):
        s = sg.suggest("Notification", None, "Claude vuole eseguire: rm -rf build/", "permission_prompt")
        self.assertIn("rm -rf build/", s[0])

    def test_limit_includes_reset_time(self):
        s = sg.suggest("StopFailure", None, "You've hit your session limit · resets 7:20pm (Europe/Rome)",
                       limit_hit=True)
        self.assertIn("7:20pm", s[0])

    def test_never_more_than_three_and_never_empty_or_duplicated(self):
        for msg in ("", "ok", "Fatto.", "x" * 500, "Vuoi che proceda? Errore: fallito il test.\nDocumentazione e README."):
            s = sg.suggest("Stop", None, msg)
            self.assertTrue(1 <= len(s) <= 3, (msg, s))
            self.assertEqual(len(s), len({x.lower() for x in s}))

    def test_generic_fallback_has_no_invented_context(self):
        s = sg.suggest("Stop", None, "Fatto.")
        self.assertEqual(s, ["Riassumi cosa hai fatto in 5 righe.", "Esegui i test e dammi solo l'esito."])


if __name__ == "__main__":
    unittest.main()
