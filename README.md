# ccmail

**E-mail notifications for [Claude Code](https://code.claude.com), with a local settings web app.**
A small daemon on your own computer mails you when Claude finishes a long task, needs a permission, hits an error or a
usage limit, or when your background processes finish – and you can answer from your phone by replying to the mail.

[![CI](https://github.com/lobascioemanuele-droid/ccmail/actions/workflows/ci.yml/badge.svg)](https://github.com/lobascioemanuele-droid/ccmail/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

- **Few mails, on purpose.** Every mail passes several gates (turn length, cooldown, hourly cap, event types). Turning
  it on or off sends nothing.
- **Private by design.** Standard library only, no telemetry, nothing leaves your machine except the mail itself, sent
  by your own mail account. The password lives in your system's credential store.
- **Reply to act.** Reply `OFF`, `ON` or `PAUSE 60` to a notification, or – if you enable it – write a sentence and it
  is delivered to the Claude chat that sent the mail.
- **Understands your chats.** Mails carry the project name, the chat title and your Claude account name, and a short
  summary of what Claude said (never cut mid-sentence).
- **Works while you are away.** While background tasks run, Claude is woken at a period you choose, only if tasks exist,
  to report their status.

> **Status: beta.** Windows is the tested platform. macOS and Linux are implemented and covered by unit tests in CI,
> but have had little real-world use – please report what you find. Gmail is the tested provider; Outlook, iCloud and
> Yahoo presets are provided and untested.

## Requirements

- Python 3.10 or newer, and [Claude Code](https://code.claude.com) (CLI or desktop app).
- A mail account that allows SMTP with an **app password** (Gmail, Outlook, iCloud, Yahoo or any SMTP server).
- For replies by mail: IMAP access to the same mailbox.

## Install

```bash
pipx install ccmail          # or: pip install ccmail
```

Until the package is on PyPI, install from the repository: `pipx install git+https://github.com/lobascioemanuele-droid/ccmail`.
ccmail is only ever installed with pip/pipx – there is no `curl | sh` or `irm | iex` installer.

## Quick start

Run these in your own terminal. ccmail never asks Claude to handle your password.

```bash
ccmail init                       # 1. sender address, provider, app password (hidden prompt)
ccmail install-hooks --autostart  # 2. add the hooks to Claude Code and start the daemon at logon
ccmail install-skill              # 3. install the /mail-notify skill (optional but recommended)
ccmail on                         # 4. turn notifications on (sends no mail)
ccmail test                       # 5. send ONE test mail (optional)
ccmail open                       # settings web app, on 127.0.0.1 only
```

Then **restart Claude Code** – hooks are read at startup. If something is off, run `ccmail doctor`: it checks the
installation and tells you what to fix, without printing any secret.

Use an *app password*, not your normal password: [Gmail](https://myaccount.google.com/apppasswords) (needs 2-step
verification), [Outlook](https://account.live.com/proofs/AppPassword), [iCloud](https://account.apple.com/account/manage),
[Yahoo](https://login.yahoo.com/account/security). `ccmail init` shows the right link for your provider.

## What you get

| Mail | When |
|---|---|
| Task finished | A turn longer than 2 minutes (setting) ended |
| Needs your input | A permission prompt or a question is waiting |
| Error / usage limit | An API error, or the account limit was reached (with the reset time) |
| Background processes | Periodic status while they run; one mail when the last one ends |
| Chat stalled | Optional watchdog: no events for N minutes with tasks declared |

Each mail has a header (project, account, chat, device), the outcome, background processes, **suggested commands**
tailored to the message (paste one into the chat), and – if reply control is on – instructions to reply.

### Settings web app

`ccmail open` shows: an overview with health checks; rules (what, how often, how much text); an activity view with
**per-project on/off switches** and the latest chats; and the connection page with provider presets and a test button.
It listens on `127.0.0.1` only, with a one-time sign-in link, CSRF protection and a strict CSP. The page is in
Italian; mails and the command line follow the `language` setting (`auto`, `it`, `en`).

### Reply by mail

Off by default (web app → Regole → Comandi suggeriti e risposta via email). When on, ccmail reads your mailbox over
IMAP and accepts only replies that: come from you, are verified by the provider, quote a valid reference from a recent
ccmail mail, and start with `OFF`, `ON` or `PAUSE [minutes]`. Optionally, any other reply text is delivered to the chat
the mail came from. See [SECURITY.md](SECURITY.md) for the exact rules.

### The `/mail-notify` skill

`ccmail install-skill` copies a small skill to `~/.claude/skills/mail-notify/`. It lets you type `/mail-notify on`,
`off`, `status`, `test` or `open` in Claude Code and tells Claude how to behave during long processes (concise status,
short final summary). It never reads credentials or settings.

## Several computers

Every computer has its own daemon, data folder and credentials, and its own copy of the settings. Give each one a
name (`ccmail init`, or web app → Connessione → *Nome di questo dispositivo*) and it appears as `Device:` in the mails.
A reply is applied by the daemon that sent the mail it refers to; daemons ignore references they did not issue.

## Platforms

| | Windows | macOS | Linux |
|---|---|---|---|
| Data folder | `%APPDATA%\ccmail` | `~/Library/Application Support/ccmail` | `~/.config/ccmail` |
| Password store | Credential Manager (fallback: DPAPI file) | Keychain (fallback: 0600 file) | Secret Service via `secret-tool` (fallback: 0600 file) |
| Start at logon | Task Scheduler | LaunchAgent | `systemd --user` (fallback: XDG autostart) |
| Status | tested | beta | beta |

The fallback files are announced loudly by `init`, `status`, `doctor` and the web app. On Linux install
`libsecret-tools` to use the Secret Service. Claude Code's folder honours `CLAUDE_CONFIG_DIR`.

## Privacy and security

- ccmail reads only what Claude Code hands to its hooks, the desktop app's chat titles and folders, and – for your
  account name – the display name from `~/.claude.json`. Nothing else in that file is used.
- Message text is redacted for common secrets before it is put in a mail; `title` and `minimal` content modes send
  no model-written text at all.
- The password is never written to logs, the database, the repository or the web app; the API never returns it.
- The daemon binds to `127.0.0.1`, authenticates hooks with a file-based token and checks `Host`/`Origin`.

Details and the threat model: [SECURITY.md](SECURITY.md). Report vulnerabilities privately (see that file).

## Troubleshooting

| Symptom | Try |
|---|---|
| No mail arrives | `ccmail doctor --network`, then `ccmail test`; check spam; check the app password |
| Hooks do nothing | `ccmail install-hooks`, then restart Claude Code; `ccmail doctor` flags a moved Python |
| Chat names missing | `ccmail diagnose` shows where the desktop app's metadata is looked for |
| Port busy | The daemon picks a free port by itself; hooks and CLI read it from `daemon.json` |
| Reply by mail is slow | Most of the delay is the provider delivering mail to yourself (seconds to ~25 s) |
| Linux: password in a file | Install `libsecret-tools` and run `ccmail init` again |

## Commands

`init` · `on` · `off` · `status [--cwd DIR]` · `test` · `open` · `stop` · `doctor [--network]` · `install-hooks
[--autostart]` · `uninstall-hooks [--autostart]` · `install-skill` · `uninstall-skill` · `diagnose` · `trace on|off|show`
· `migrate` · `daemon [--foreground]`. If `ccmail` is not on `PATH`, use `python -m ccmail …`.

## How it works

Hooks run a tiny client (`python -m ccmail hook`) that forwards the event to the local daemon and always exits 0; the
daemon applies the rules, writes to SQLite, and an outbox worker sends mail with retries. More in
[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

## Development

```bash
git clone https://github.com/lobascioemanuele-droid/ccmail && cd ccmail
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
python -W ignore -m unittest discover -s tests     # set PYTHONPATH=src:tests if not installed
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE).
