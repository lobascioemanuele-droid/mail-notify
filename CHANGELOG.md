# Changelog

All notable changes to ccmail. Versions follow [Semantic Versioning](https://semver.org/).

## 0.6.0

### Added
- **Cross-platform daemon**: data folders for Windows, macOS and Linux; credentials in Windows Credential Manager,
  macOS Keychain or Linux Secret Service (password through stdin only), with an announced 0600-file fallback;
  autostart through Task Scheduler, LaunchAgent or `systemd --user` (XDG autostart as last resort).
- Provider presets (Gmail, Outlook/Microsoft 365, iCloud, Yahoo, custom) in `ccmail init` and the web app.
- `language` setting (`auto`/`it`/`en`): mails, suggested commands, messages delivered to Claude and the command line.
  `PAUSE` is accepted next to `PAUSA`.
- `device_name` setting: a `Device:` line in mails when ccmail runs on several computers.
- `ccmail doctor [--network]`: installation check that prints no secrets (credentials, hooks, interpreter, skill,
  autostart, daemon, mail servers).
- `ccmail install-skill` / `uninstall-skill`: the `mail-notify` skill ships inside the package.
- `CLAUDE_CONFIG_DIR` is honoured for settings, skills, transcripts and the account name.
- The daemon picks a free port when the configured one is taken by another program.
- Community files, CI matrix (Windows/macOS/Linux x Python 3.10-3.13), release workflow with PyPI trusted publishing.

### Changed
- Detached daemon start on POSIX uses a new session; `pythonw.exe` only on Windows.
- The design notes moved from the README to `docs/HOW-IT-WORKS.md`; working notes to `docs/dev/`.

## 0.5.0

### Added
- Reply-by-email control channel (`inbound_enabled`): `OFF` / `ON` / `PAUSA [min]` in the first line of a reply.
- Optional delivery of free-text replies into the Claude chat (`inbound_to_claude`, off by default).
- Mails are split into sections (outcome, background processes, suggested commands, management) and carry
  `Chat: <name>`; account name comes from the Claude account.
- Usage-limit reached is its own event; summaries never cut a sentence in the middle.
- `ccmail diagnose` shows the reply channel state.

### Fixed
- Replies sent to oneself were rejected as unauthenticated: Gmail's Sent label is now used.
- The test mail was not recorded, so the app kept saying "SMTP not yet verified".

## 0.3.0

### Added
- Chat names: the daemon reads the Claude desktop app's per-chat metadata (title, project folder, archived flag, last activity, and the Claude Code session id). Mails show `Chat: <name>`; unknown chats show `Sessione: <8 characters>`.
- Title fallback: when app metadata is missing, the title is the latest `custom-title` record at the end of the session transcript (read from the tail of the file only, cached).
- Discovery of app metadata in both the Microsoft Store package location (`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude`) and `%APPDATA%\Claude`, merged by session id. Override with `CCMAIL_CLAUDE_SESSIONS` (paths separated by `;`).
- Stable project resolution: chat folder, then a chat whose folder matches the transcript folder, then `CLAUDE_PROJECT_DIR`, then the shortest working folder seen for that transcript folder. Sub-folders are never projects; sub-agent transcripts are excluded.
- Startup repair: realigns stored sessions to their projects and removes projects with no sessions or chats; per-project switches are kept.
- Activity API: `GET /v1/projects?chats=N` (default 5 newest non-archived chats per project, `0` = all; archived chats listed separately) and `GET /v1/diagnostics`.
- `ccmail diagnose`: read-only report of what ccmail sees in the desktop app.
- Per-project on/off switch (`PUT /v1/projects/enabled`). When off, no mail and no wake-ups for that project. `ccmail status --cwd <folder>` reports it.
- Honest session states: working, waiting (only if recent), waiting for background processes, periodic check, quiet (idle over 6 h), ended, archived, stale, uncertain.
- `PostToolBatch` hook (async) for liveness.

### Changed
- Projects are derived from the chat's folder instead of the current working folder.

### Upgrade notes
- Run `ccmail install-hooks` again and restart Claude Code (the new `PostToolBatch` hook).

### Known limits
- The desktop app's metadata format is internal and undocumented. Every read soft-fails: if the format changes, ccmail falls back to hook data.
- A chat could in theory change its Claude Code session id after resume or compaction. Not observed so far.

## 0.2.0

### Added
- Background-task awareness: while tasks run in the background (`Stop.background_tasks`), intermediate `Stop` events send no mail.
- Periodic wake-up: every `heartbeat_minutes`, and only while tasks are still running, the daemon wakes Claude through an `asyncRewake` Stop hook (`ccmail wake`). Claude checks the tasks and its short reply is mailed as a status update. A `skill` mode is available where Claude schedules its own wake-ups.
- "Finished" mail: one mail when the last background task disappears.
- Watchdog rule: an optional silent-session watchdog (`watchdog_minutes`), which must be off or at least twice `heartbeat_minutes`.
- Summary mode (`content_mode = summary`, default): a short extractive summary of the turn, capped by `summary_chars`.
- Minimal mode (`content_mode = minimal`): no model-derived text at all.
- Account name (`account_name`), shown in every mail as `Account:`; empty = Windows user name.

## 0.1.0

### Added
- Local daemon for Windows that emails about Claude Code events through Gmail SMTP, with a local configuration web app on `127.0.0.1`.
- Hooks (`Stop`, `Notification`, `StopFailure`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PostToolUse` for `Bash`) forwarded to the daemon; all decisions are made in the daemon.
- Anti-spam gates: no confirmation mails, minimum turn length for `Stop`, mute after toggle, per-session cooldown, hourly cap, selectable `Notification` types, title-only mode.
- Credentials in Windows Credential Manager, with a DPAPI-encrypted file fallback.
- Commands: `init`, `on`, `off`, `status`, `test`, `open`, `stop`, `daemon`, `install-hooks`, `uninstall-hooks`, `migrate`.
