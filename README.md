# ccmail

A small local daemon for Windows that emails you (Gmail SMTP) about [Claude Code](https://code.claude.com) events, with a local web app for configuration. Python ≥ 3.10, standard library only, MIT licensed.

**Design goal: as few emails as possible.** A mail is sent only when *all* gates pass:

- no confirmation mails — `init`, `on`, `off` never send anything (only an explicit `ccmail test` does);
- `Stop` mails only for turns longer than `min_elapsed_s` (default 120 s);
- silence for `mute_after_toggle_s` (60 s) after switching on/off;
- at most one mail per session every `cooldown_s` (60 s);
- hard cap `max_per_hour` (10) across sessions;
- `Notification` only for the types you select (default: `permission_prompt`, `agent_needs_input`);
- "title only" mode if you don't want message text in your inbox.

## Install

```powershell
pipx install git+https://github.com/<you>/ccmail     # or: pip install .
ccmail init            # you run this: SMTP host, Gmail address, App Password (hidden prompt)
ccmail install-hooks --autostart
ccmail on
ccmail open            # configuration web app on 127.0.0.1
```

If `ccmail` is not on `PATH`, use `python -m ccmail ...`. No `curl | sh` / `irm | iex` installers, ever.

Use a Gmail [App Password](https://support.google.com/accounts/answer/185833) (requires 2-Step Verification). The password is stored in Windows Credential Manager (generic credential `ccmail:smtp`); if that fails it falls back to a DPAPI-encrypted file in `%APPDATA%\ccmail` (bound to your Windows user, ACL-restricted) and warns you. It is never written to logs, the database, the repo or the web app.

## How it works

Hooks are tiny: `python -m ccmail hook` forwards the hook JSON to the daemon on `127.0.0.1` and always exits 0 (if the daemon is down it starts it, and as a last resort spools the event to disk). All decisions — filtering, cooldowns, redaction, retries — happen in the daemon.

Hooked events: `UserPromptSubmit` (records turn start only), `Stop`, `Notification`, `StopFailure`, `SessionStart`, `SessionEnd`, `PostToolUse` for `Bash` (async; notices background tasks), and `PostToolBatch` (async; liveness only, since 0.3.0). After upgrading, run `ccmail install-hooks` again and restart Claude Code.

### Background processes (v0.2)

- While background tasks are running (`Stop.background_tasks`), intermediate `Stop` events send **no** mail.
- Every `heartbeat_minutes` (web app → Regole → Processi in background), and only if tasks are still running, the daemon wakes Claude through an `asyncRewake` Stop hook (`ccmail wake`, exit code 2 → system reminder). Claude queries the tasks and its short reply is mailed as a status update.
- When the last task disappears from `background_tasks`, one "finished" mail is sent.
- If waking an idle session does not work on your setup, set the mode to `skill`: Claude then schedules its own wake-ups (see the `mail-notify` skill).
- Unknown turn duration counts as short: wake-up turns never mail through the ordinary path.

## Chats and projects (v0.3)

- **Chat names.** The daemon reads the Claude desktop app's per-chat metadata (`claude-code-sessions\<account>\<org>\local_*.json`: title, project folder, archived flag, last activity). Mails show `Chat: <name>`; if the name is unknown they show `Sessione: <first 8 characters of the session id>`. If the app metadata is missing, the title falls back to the latest `custom-title` record at the end of the session transcript.
- **Where the metadata is found.** The desktop app (Microsoft Store package) and Store Python are both virtualized, so ccmail looks in `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code-sessions` and in `%APPDATA%\Claude\claude-code-sessions`, and merges both. Override with the environment variable `CCMAIL_CLAUDE_SESSIONS` (several paths separated by `;`).
- **Project of a session**, strongest evidence first: the chat's project folder; a known chat whose folder matches the transcript folder; `CLAUDE_PROJECT_DIR`; the shortest working folder seen for that transcript folder. Sub-folders are never separate projects. At startup ccmail repairs stored data: it realigns sessions, removes projects with no sessions or chats, and keeps your per-project switches.
- **Activity view.** `GET /v1/projects?chats=N` returns the 5 newest non-archived chats per project by default (`chats=0` = all); archived chats are listed separately. `GET /v1/diagnostics` and `ccmail diagnose` (read-only) show what ccmail can see: metadata locations, chat and session counts.
- **Per-project switch.** `PUT /v1/projects/enabled` turns a project on or off. When a project is off, ccmail sends no mail and no wake-ups for it. `ccmail status --cwd <folder>` shows that project's switch.
- **Session states** are stated only as far as the evidence goes: *Al lavoro* (working; "no signal" after a long silence), *In attesa di te* (finished its turn, only if recent), *Attende i processi in background*, *Controllo periodico*, *Inattiva* (idle for more than 6 hours), *Terminata*, *Archiviata*, *Nessun segnale* (stale), *Stato non certo* (uncertain).

The desktop app's metadata format is internal and undocumented: if it changes, ccmail reads nothing from it and falls back to hook data (it never fails because of it).

## Commands

`init`, `on`, `off`, `status` (`--cwd <folder>` also shows that project's switch), `test`, `open`, `stop`, `daemon`, `diagnose` (read-only report of what ccmail sees in the desktop app), `install-hooks`, `uninstall-hooks`, `migrate` (removes the old `email_notify.py` hooks without reading its credential file).

## Development

```powershell
$env:PYTHONPATH="src;tests"; python -m unittest discover -s tests
```

See [SECURITY.md](SECURITY.md).
