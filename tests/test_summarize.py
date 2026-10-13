import unittest

from ccmail.summarize import summarize

LONG = """## Fase 1 pronta

Ho completato il refactoring del modulo di pagamento e aggiunto la validazione degli importi. Tutti i test passano.

### Cosa è cambiato
- **Validazione:** gli importi negativi vengono rifiutati con un errore chiaro.
- **Arrotondamento:** ora usa `Decimal` invece di `float`.
- Aggiornati i fixture dei test di integrazione.
- Migrata la tabella `payments` (nuova colonna `currency`).
- Rimosso il vecchio client HTTP.
- Documentazione aggiornata.
- Changelog aggiornato.

```python
def pay(x):
    return x
```

| col | val |
|---|---|
| a | 1 |

Vuoi che apra una PR con queste modifiche?"""


class SummarizeTests(unittest.TestCase):
    def test_long_message_is_short_and_keeps_the_point(self):
        s = summarize(LONG, 600)
        self.assertLessEqual(len(s), 600)
        self.assertIn("Ho completato il refactoring", s)
        self.assertIn("Cosa è cambiato: Validazione", s)  # one line per section: heading + what it opens with
        self.assertTrue(s.rstrip().endswith("PR con queste modifiche?"))
        self.assertNotIn("def pay", s)
        self.assertNotIn("|---|", s)
        self.assertNotIn("**", s)
        self.assertLessEqual(len(s.splitlines()), 10)

    def test_covers_the_whole_message_in_at_most_ten_lines(self):
        text = "Ho finito la verifica.\n\n" + "\n".join(
            f"## Sezione {i}\nIn questa sezione ho controllato il punto {i} e tutto torna." for i in range(1, 21))
        s = summarize(text, 3000)
        lines = s.splitlines()
        self.assertLessEqual(len(lines), 10)
        self.assertIn("Sezione 1:", s)
        self.assertIn("Sezione 20:", s)  # the end of the message is represented, not only the beginning
        self.assertRegex(s, r"\(\+\d+ altri punti\)")

    def test_sections_and_closing_question(self):
        text = ("Il lavoro è completo.\n\n## Merge\n- Fatto il merge su main.\n- I test passano.\n\n"
                "## Limiti\nNon ho provato macOS.\n\nVuoi che pubblichi?")
        s = summarize(text, 1200).splitlines()
        self.assertEqual(s[0], "Il lavoro è completo.")
        self.assertTrue(s[1].startswith("Merge: Fatto il merge su main."))
        self.assertEqual(s[2], "Limiti: Non ho provato macOS.")
        self.assertEqual(s[-1], "Vuoi che pubblichi?")

    def test_a_closing_paragraph_does_not_become_the_section_body(self):
        text = "Ciao.\n\n## Esito\n- Primo punto.\n- Secondo punto.\n\nNota finale non importante."
        self.assertIn("Esito: Primo punto.", summarize(text, 1200))

    def test_table_is_named_by_its_first_column(self):
        text = ("Fatto.\n\n## Stato dei branch\n| Branch | Cosa contiene |\n|---|---|\n| web-language | lingua |\n"
                "| summary | riassunto |\n\nDimmi com'è.")
        s = summarize(text, 800)
        self.assertIn("Stato dei branch: web-language, summary", s)
        self.assertNotIn("|", s)

    def test_lines_are_short(self):
        text = "Ok.\n\n" + "\n".join(f"## S{i}\n" + ("Una frase molto lunga " * 20) + "." for i in range(3))
        for line in summarize(text, 800).splitlines():
            self.assertLessEqual(len(line), 160, line)

    def test_default_budget_of_300_characters_still_covers_the_answer(self):
        text = "Ho finito la verifica completa del modulo e di tutte le sue parti.\n\n" + "\n".join(
            f"## Sezione {i}\nIn questa sezione ho controllato il punto {i} con molta attenzione e tutto torna." for i in range(1, 9)
        ) + "\n\nVuoi che proceda con la pubblicazione?"
        s = summarize(text, 300)
        self.assertLessEqual(len(s), 300)
        lines = s.splitlines()
        self.assertGreaterEqual(len(lines), 3)  # lead, at least one section, the question
        self.assertTrue(lines[0].startswith("Ho finito la verifica"))
        self.assertTrue(lines[-1].endswith("pubblicazione?"))

    def test_question_that_is_the_lead_is_not_repeated(self):
        self.assertEqual(summarize("Vuoi che proceda con la pubblicazione?", 300), "Vuoi che proceda con la pubblicazione?")
        self.assertEqual(summarize("Ho finito. Vuoi che pubblichi?", 300), "Ho finito. Vuoi che pubblichi?")

    def test_plain_text_only(self):
        s = summarize("Fatto. Ho sistemato il bug nel parser e aggiunto un test. Altre note lunghe " * 20, 300)
        self.assertLessEqual(len(s), 300)
        self.assertTrue(s.startswith("Fatto."))

    def test_empty_and_code_only(self):
        self.assertEqual(summarize("", 600), "")
        self.assertEqual(summarize("```\nx = 1\n```", 600), "")

    def test_hard_cap(self):
        self.assertLessEqual(len(summarize("- " + "parola " * 500, 100)), 100)


if __name__ == "__main__":
    unittest.main()
