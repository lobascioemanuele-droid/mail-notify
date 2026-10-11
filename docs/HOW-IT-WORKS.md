# How ccmail works

Design notes: hooks, daemon, background processes, chats and projects. For installation and usage see the [README](../README.md); for the security model see [SECURITY.md](../SECURITY.md).

## Hooks and daemon

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

