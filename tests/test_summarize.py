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
        self.assertIn("• Validazione", s)
        self.assertIn("altri punti", s)
        self.assertTrue(s.rstrip().endswith("PR con queste modifiche?"))
        self.assertNotIn("def pay", s)
        self.assertNotIn("|---|", s)
        self.assertNotIn("**", s)
        self.assertNotIn("Cosa è cambiato", s)  # headings don't take bullet slots
        self.assertLess(len(s), 500)

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
