# Contributing

Thanks for helping. ccmail is deliberately small: standard library only, no runtime dependencies. Please keep it so.

## Setup

```bash
git clone https://github.com/lobascioemanuele-droid/mail-notify && cd mail-notify
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
PYTHONPATH=src:tests python -W ignore -m unittest discover -s tests     # Windows: set PYTHONPATH=src;tests
```

`tests/test_matrix.py` runs every mail kind x content mode x language x reply option and compares the
policy with the web app's dry run: extend its tables when you add a kind, a mode or a setting.

Tests isolate the data folder (`CCMAIL_HOME`), the desktop-app metadata (`CCMAIL_CLAUDE_SESSIONS`) and the account
name, and pin `CCMAIL_LANG=it`. They never touch your real credentials or `~/.claude`.

## Rules

- **Never** handle a real password in code, tests, logs or issues. Use fake values.
- No personal paths, names or addresses in code, tests or docs.
- Texts a user can see exist in Italian and English: write them as an `(it, en)` pair with `i18n.pick`.
- Anything that can send mail, or can reach Claude, needs a test for the case where it must *not* happen.
- Platform code goes through the small seams that already exist (`paths`, `credstore`, `autostart`) so it can be tested
  on any OS.
- Update `CHANGELOG.md`. Add docs when behaviour changes, and `docs/RESOURCES.md` when a change alters what ccmail
  sends to the model, polls, or runs.

## Security issues

Report them privately: see [SECURITY.md](SECURITY.md).
