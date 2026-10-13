# Changelog

All notable changes to ccmail. Versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Changed (summary)
- The summary in `summary` mode now covers the whole answer in at most 10 short lines: a lead, one line per
  section ("Heading: what it opens with") sampled evenly across the message, and the closing question. The default
  `summary_chars` is 1200. The scrollable box of the HTML mail holds the complete answer (in `full` mode, the part
  that is not already shown above it).
- `install-skill` and `install-hooks` also create the short `mail-notify` command, so there is no third step.
- Summary lines are short (about 130 characters; one bullet per section; a table is named by its first column),
  the default `summary_chars` is 800, and the box no longer repeats the sentences the answer starts with.
- The scrollable box formats the Markdown of the answer: fenced code as a code block, inline code, bold, headings and
  bullets (everything escaped; the plain-text part is unchanged).

### Added (command name)
- `mail-notify` is a command of its own (console script next to `ccmail`), and `install-cli` / `uninstall-cli` write
  or remove a small launcher in a folder already on `PATH`, for setups where the Scripts folder is not (Microsoft
  Store Python). The launcher only ever overwrites or deletes a file it wrote itself.

### Added (language)
- The web app is available in English: the `language` setting now covers the page, the mails and the command line.
  `auto` follows Claude Code's own `language` setting (Italian/English), then the system language. Catalogs are
  static files (`web/lang-en.js`, `web/lang-en-app.js`), applied without network access; a test checks that every
  catalog entry still exists in the page.

### Fixed
- Several commands arriving in one pass (for example ON then OFF) are applied oldest first, so the project ends
  in the state asked for last; before, the newest was applied first and the older one won.

### Added
- A verified reply that cannot be delivered (no chat tied to the mail, unknown or expired reference, delivery to
  Claude switched off) is answered with a mail in the usual layout whose OUTCOME says "reply discarded" and why.
  Capped at 5 an hour; ccmail's own mails are never answered, so no loop.
- `html_mail` setting (off by default): mails carry an HTML alternative next to the unchanged plain text, with
  quick-reply buttons (OFF / ON / PAUSE 60, and "Write to Claude") that are pre-filled `mailto:` replies, and the
  whole message in a box with its own scroll bar. The box follows `content_mode` (nothing extra in `title`/`minimal`).
  One-click actions without pressing Send are not possible with a loopback-only daemon (Gmail actions need
  registration with Google and a public HTTPS endpoint).

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
- `docs/RESOURCES.md`: measured token, CPU, memory, disk, network and latency costs.
- Combination tests (`tests/test_matrix.py`): every mail kind x content mode x language x reply options, and policy vs. dry run.
- Community files, CI matrix (Windows/macOS/Linux x Python 3.10-3.13), release workflow with PyPI trusted publishing.

### Fixed
- The answer mail of an e-mail reply was still skipped as a "stop hook loop": the reply reaches the chat through a
  Stop hook, so its turn ends with `stop_hook_active` set. The guard no longer applies to that one-shot turn.
- The IMAP scan fetched one header per message each pass; it now asks for all of them in a single command.
- The answer to a reply sent by e-mail was never mailed back when the turn was short or processes were still running
  (the user, being away, never saw it). A turn started by an e-mail reply is now always mailed at its end, with the
  subject "risposta alla tua email"; the per-project switch, snooze and hourly cap still apply.

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
