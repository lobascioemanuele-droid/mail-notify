# ccmail

**E-mail notifications for [Claude Code](https://code.claude.com), with a local settings web app.**
A small daemon on your own computer mails you when Claude finishes a long task, needs a permission, hits an error or a
usage limit, or when your background processes finish. You can answer from your phone by replying to the mail.

[![CI](https://github.com/lobascioemanuele-droid/ccmail/actions/workflows/ci.yml/badge.svg)](https://github.com/lobascioemanuele-droid/ccmail/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

- **Few mails, on purpose.** Every mail passes several gates (turn length, cooldown, hourly cap, event types). Turning
  it on or off sends nothing.
- **Private by design.** Standard library only, no telemetry. The only thing that leaves your machine is the mail
  itself, sent through your own mail account. The password lives in your system's credential store.
- **Reply to act.** Reply `OFF`, `ON` or `PAUSE 60` to a notification or, if you enable it, write a sentence that is
  delivered to the Claude chat that sent the mail.
- **Understands your chats.** Mails name the project, the chat and your Claude account, and carry a short summary of
  what Claude said (about 300 characters, at most 10 lines; the complete answer follows in a scrollable box).
- **Works while you are away.** While background tasks run, Claude is woken at a period you choose, only when tasks
  exist, to report their status.

> **Tested today: Windows with Gmail.** macOS, Linux and mail providers other than Gmail (the Outlook, iCloud and
> Yahoo presets) are implemented and covered by the automated tests in CI, but are **not yet tested on a real
> system: that is planned for a later release.** Please report what you find.

## Install step by step

You need your mail account's app password and about fifteen minutes. Run every command in **your own terminal**.
ccmail never asks Claude for your password.

### Requirements

- Python 3.10 or newer.
- [Claude Code](https://code.claude.com), as the CLI or the desktop app.
- A mail account that allows SMTP with an **app password**. Presets exist for Gmail, Outlook, iCloud and Yahoo, and
  you can enter any SMTP server.
- Optional, for replies by mail: IMAP access to the same mailbox.

### Step 1: install the package

Install with [pipx](https://pipx.pypa.io), which keeps the program in its own environment. Once ccmail is on PyPI:

```bash
pipx install ccmail
```

To install the current version from the repository instead:

```bash
pipx install git+https://github.com/lobascioemanuele-droid/ccmail
```

With Microsoft Store Python the `mail-notify` command can be "not found", because its Scripts folder is not on
`PATH`. In that case use `python -m ccmail` instead of `mail-notify` in every step below, or run
`python -m ccmail install-cli` once: it creates a short `mail-notify` command in a folder that is already on `PATH`.

### Step 2: set up the sender account

```bash
mail-notify init
```

The prompts ask for the sender address, the provider (the right app-password link is shown for it), the app password,
the recipient (it defaults to the sender) and an optional device name. The app password is typed in a **hidden**
prompt and stored only in the system credential store (Windows Credential Manager, macOS Keychain or Linux Secret
Service). Never paste it into a chat, a file or an issue. This step sends no mail.

Where to create an app password:

- Gmail: https://myaccount.google.com/apppasswords (needs 2-step verification)
- Outlook: https://account.live.com/proofs/AppPassword
- iCloud: https://account.apple.com/account/manage
- Yahoo: https://login.yahoo.com/account/security

### Step 3: install the hooks and the daemon

```bash
mail-notify install-hooks --autostart
```

This adds ccmail's hooks to Claude Code's settings (a backup of the file is made first), starts the background daemon
at logon, and creates the short `mail-notify` command if it is missing. Nothing is sent.

### Step 4: install the skill, then restart Claude Code

```bash
mail-notify install-skill
```

Restart Claude Code afterwards: hooks and skills are read at startup.

### Step 5: turn it on and test it

```bash
mail-notify on
```

Notifications are **off** after installation, so nothing is sent until you run this. `on` sends no mail.

```bash
mail-notify test
```

This sends **one** test mail. It also works while notifications are off.

```bash
mail-notify open
```

This opens the settings web app in your browser. It listens on `127.0.0.1` only and uses a one-time sign-in link.
Optionally, in its **Rules** tab, turn on:

- **Control by replying to the e-mail** (reply `OFF` / `ON` / `PAUSE`; needs IMAP, see above).
- **Replies reach Claude** (free-text replies are delivered to the chat that sent the mail).
- **E-mail with buttons** (an HTML version with tappable quick-reply cards).

### Step 6: use it

In Claude Code, the `/mail-notify` skill accepts `on`, `off`, `status`, `test` and `open`:

```bash
/mail-notify on
```

```bash
/mail-notify status
```

Each project also has its own switch in the web app's **Activity** tab.

When reply control is on, each mail tells you how to reply. Write one of these on the **first line** of your reply:

- `OFF`: turns notifications off for that project.
- `ON`: turns them back on for that project.
- `PAUSE 60` (or `SLEEP`, or `PAUSE` alone for 60 minutes): silence on all projects for that many minutes.

### Updating

Update the package (use the command that matches how you installed it):

```bash
pipx upgrade ccmail
```

```bash
pipx install --force git+https://github.com/lobascioemanuele-droid/ccmail
```

Then refresh the hooks and the skill, and restart Claude Code:

```bash
mail-notify install-hooks
```

```bash
mail-notify install-skill
```

### Uninstalling

Stop the daemon, then remove the pieces in this order:

```bash
mail-notify stop
```

```bash
mail-notify uninstall-hooks --autostart
```

```bash
mail-notify uninstall-skill
```

```bash
mail-notify uninstall-cli
```

```bash
pipx uninstall ccmail
```

Uninstalling does not delete the data folder or the stored password. Delete the data folder yourself (on Windows
`%APPDATA%\ccmail`), remove the ccmail entry from your credential store (on Windows it is `ccmail:smtp` in Credential
Manager), and revoke the app password at your mail provider.

## What a mail looks like

- **Header:** `Project:`, `Account:` and `Chat:` (the chat's name in the desktop app, or `Session:` with a short id when
  the name is unknown). `Device:` appears when you set a device name. Turn duration and type follow for a finished turn.
- **OUTCOME** (`ESITO` in Italian): a short summary of the whole answer, about 300 characters (the `summary_chars`
  setting) and at most 10 lines: a lead, one line per section, and the closing question when the answer ends with one.
  With HTML mail on, the complete answer is in a scrollable box below it.
- **Background processes:** how many are still running, and their ids, when there are any.
- **Suggested commands:** a numbered list tailored to the message, to paste into the chat. They are tappable boxes
  when "Replies reach Claude" is on.
- **Quick-reply cards** (HTML mail with reply control on): `OFF`, `ON` and `SLEEP` (a 1-hour pause on all projects),
  other pauses (15 minutes, 3 hours, 8 hours) and, if "Replies reach Claude" is on, "Write to Claude". Each card opens
  a pre-filled reply; you still press Send.
- **Reply instructions** in plain text, when reply control is on.

## What you get

| Mail | When |
|---|---|
| Task finished | A turn longer than 2 minutes (setting) ended |
| Needs your input | A permission prompt or a question is waiting |
| Error / usage limit | An API error, or the account limit was reached (with the reset time) |
| Background processes | Periodic status while they run; one mail when the last one ends |
| Chat stalled | Optional watchdog: no events for N minutes with tasks declared |

### HTML mail (optional)

Turn on **E-mail with buttons** in the web app's Rules tab and every mail also carries an HTML version next to the
unchanged plain text: a coloured header by event type, the data in a grid, quick-reply cards, the suggested commands
as tappable boxes (when "Replies reach Claude" is on), and the whole message in a box with its own scroll bar (at most
340 px tall). The same layout is used for the confirmation, "command executed" and "reply discarded" mails.

- The cards are pre-filled replies (`mailto:`): you still press Send. One-click actions inside Gmail need registration
  with Google and a public HTTPS endpoint, which a daemon listening on loopback cannot offer.
- A mail app that does not show HTML shows the plain text, without cards or the scroll box.
- Nothing extra leaves your machine: the box follows the content mode (`title` and `minimal` add nothing), and text
  goes through the same secret redaction.
- A verified reply that cannot be delivered (no chat tied to the mail, unknown or expired reference, delivery to
  Claude switched off) is answered with a mail whose outcome says "reply discarded" and why. At most 5 an hour.

### Settings web app

`mail-notify open` shows: an **Overview** with health checks and the main on/off switch; **Rules**
(what to report, how often, how much text, reply control); **Activity** with per-project switches and the latest chats;
and **Connection** with provider presets, the device name and a test button. It listens on `127.0.0.1` only, with a
one-time sign-in link, CSRF protection and a strict content security policy.

The page, the mails and the command line follow the `language` setting: `auto` (the default), `it` or `en`. `auto`
uses the language set in Claude Code's own `language` setting when it names Italian or English, and otherwise the
system language. The page is translated from static catalogs in the package. Nothing is sent to a translation
service, and data from the daemon (project names, chat titles, mail subjects) is never translated.

### Reply by mail

Off by default (web app → Rules → *Control by replying to the e-mail*). When on, ccmail reads your mailbox over IMAP
and accepts only replies that come from you, are verified by the provider, quote a valid reference from a recent
ccmail mail, and start with `OFF`, `ON` or `PAUSE [minutes]` (the Italian words `SPEGNI`, `ACCENDI`, `PAUSA`, and a few
others are accepted too). With **Replies reach Claude** on, any other reply text is delivered to the chat the mail came
from. The answer to it is mailed back to you when that turn ends, even if it was short, with the subject
"answer to your e-mail" (Italian: "risposta alla tua email").

See [SECURITY.md](SECURITY.md) for the exact rules and [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) for the delivery
paths.

### The `/mail-notify` skill

`mail-notify install-skill` copies a small skill to `~/.claude/skills/mail-notify/`. It lets you type
`/mail-notify on`, `off`, `status`, `test` or `open` in Claude Code, and tells Claude how to behave during long
processes (a concise status while they run, a short final summary). The skill is instructed never to read credential
files or edit Claude's settings.

## Several computers

Every computer has its own daemon, data folder and credentials, and its own copy of the settings. Give each one a name
(`mail-notify init`, or web app → Connection → *Name of this device*) and it appears as `Device:` in the mails. A reply
is applied by the daemon that sent the mail it refers to; daemons ignore references they did not issue.

## Platforms

| | Windows | macOS | Linux |
|---|---|---|---|
| Data folder | `%APPDATA%\ccmail` | `~/Library/Application Support/ccmail` | `~/.config/ccmail` |
| Password store | Credential Manager (fallback: DPAPI file) | Keychain (fallback: 0600 file) | Secret Service via `secret-tool` (fallback: 0600 file) |
| Start at logon | Task Scheduler | LaunchAgent | `systemd --user` (fallback: XDG autostart) |
| Status | tested | to be tested (later release) | to be tested (later release) |

The fallback files are announced loudly by `init`, `status`, `doctor` and the web app. On Linux install
`libsecret-tools` to use the Secret Service. Claude Code's folder honours `CLAUDE_CONFIG_DIR`.

## Privacy and security

- ccmail reads only what it needs: what Claude Code passes to its hooks; the desktop app's chat titles and folders; the
  latest chat title recorded at the end of a session transcript, when the app's metadata is missing; the account's
  display name from `~/.claude.json` (nothing else in that file is used); and the `language` key of Claude Code's
  `settings.json`. Installing the hooks edits that file, after making a backup.
- Message text is redacted for common secrets before it is put in a mail. `title` and `minimal` content modes send no
  model-written text at all.
- The password is never written to logs, the database, the repository or the web app; the API never returns it.
- The daemon binds to `127.0.0.1`, authenticates hooks with a file-based token and checks `Host` and `Origin`.

Details and the threat model: [SECURITY.md](SECURITY.md). Report vulnerabilities privately (see that file).

## Troubleshooting

| Symptom | Try |
|---|---|
| No mail at all | `mail-notify status`: notifications start OFF, so run `mail-notify on`. Then `mail-notify doctor --network` and `mail-notify test`; check spam and the app password |
| Hooks do nothing | `mail-notify install-hooks`, then restart Claude Code; `mail-notify doctor` flags a moved Python |
| No buttons or cards in a mail | Cards need HTML mail and reply control on; plain-text mail apps show no buttons |
| Chat names missing | `mail-notify diagnose` shows where the desktop app's metadata is looked for |
| Port busy | The daemon picks a free port by itself; hooks and CLI read it from `daemon.json` |
| Reply by mail is slow | Most of the delay is the provider delivering mail to yourself (seconds to about 25 s) |
| Linux: password in a file | Install `libsecret-tools` and run `mail-notify init` again |

## Commands

`init` · `on` · `off` · `status [--cwd DIR]` · `test` · `open` · `stop` · `doctor [--network]` · `install-hooks
[--autostart]` · `uninstall-hooks [--autostart]` · `install-skill` · `uninstall-skill` · `diagnose` · `trace on|off|show`
· `migrate` · `daemon [--foreground]` · `install-cli` · `uninstall-cli`.

The command is installed as both `mail-notify` and `ccmail`. `install-hooks` and `install-skill` also create the short
`mail-notify` command. If neither name is found, run `python -m ccmail install-cli` once (see Step 1).

## Resource use

No model calls at all in normal operation. The daemon uses about 35 MB and about 0.05–0.25 % of one core, and each hook
call adds about 0.12 s. Tokens are spent only on the skill text, on periodic wake-ups while background tasks run and on
delivered replies. The HTML mail costs no tokens (about 0.1–0.6 ms of CPU and 6–9 KB more per mail). Measurements,
formulas and limits: [docs/RESOURCES.md](docs/RESOURCES.md).

## How it works

Hooks run a tiny client (`python -m ccmail hook`) that forwards the event to the local daemon and always exits 0. The
daemon applies the rules, writes to SQLite, and an outbox worker sends mail with retries. More in
[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

## Development

```bash
git clone https://github.com/lobascioemanuele-droid/ccmail && cd ccmail
```

```bash
python -m venv .venv
```

```bash
. .venv/bin/activate
```

On Windows, activate with `.venv\Scripts\activate` instead.

```bash
pip install -e .
```

```bash
python -W ignore -m unittest discover -s tests
```

Set `PYTHONPATH=src:tests` first if the package is not installed.

See [CONTRIBUTING.md](CONTRIBUTING.md). Changes are listed in [CHANGELOG.md](CHANGELOG.md); what 1.x promises to keep
stable is in [docs/STABILITY.md](docs/STABILITY.md).

## License

[MIT](LICENSE).
