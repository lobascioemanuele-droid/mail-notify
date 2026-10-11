---
name: mail-notify
description: Turn Claude Code e-mail notifications on or off, check them, send a test mail or open the settings web app (argument: on, off, status, test, open) through ccmail, and apply the long-running-process rules while they are on
---
<!-- ccmail-skill-version: 0.6.0 -->
You manage Claude Code's e-mail notifications with **ccmail** (a local daemon). The skill argument is `$ARGUMENTS`.
Answer the user in the language they write in.

Run ccmail as `ccmail <command>`. If `ccmail` is not on the PATH, use `python -m ccmail <command>`
(`py -m ccmail` on Windows).

Allowed commands, one at a time:
- `on`: turns notifications on. It sends **no** confirmation mail.
- `off`: turns notifications off. It sends **no** confirmation mail.
- `status`: tells whether they are ON or OFF, whether the daemon runs, whether credentials and hooks exist.
- `test`: sends ONE test mail, even while notifications are off.
- `open`: opens the settings web app in the browser.

Procedure:
1. If the argument is missing or is not one of the five, answer with the list above and run nothing.
2. Otherwise run `ccmail <argument>` and report the printed outcome in one line.
3. After `on` or `off`, also run `ccmail status` and confirm the resulting state.
4. After `on` (or a `status` that says ON) say in one line that the rules below apply from now on in this session.
   After `off` say they no longer apply and, if you scheduled wake-ups with `ScheduleWakeup`, cancel them (`stop: true`).
5. If `status` reports missing credentials or hooks, do not try to fix it: tell the user to run `ccmail init` and
   `ccmail install-hooks` themselves in their own terminal. Never ask for, read or type the mail password.

## What ccmail already sends (so you know when nothing else is needed)
ccmail mails by itself at the end of a long turn (over ~2 minutes), when a permission or input is needed, on an API
error, at every periodic check of background processes and when the last background process has finished. While
processes run, intermediate answers produce no mail. Everything is rate limited. So never send mail yourself and never
schedule wake-ups for ordinary tasks.

## Per-project switch
Every project has a switch in the web app (Activity). Before applying the rules below run
`ccmail status --cwd "<current project folder>"` and read the "Project …" line: if it says the notifications are
**OFF for this project**, the long-running-process rules **do not apply** in this chat (no wake-ups scheduled by you,
no special summary) and ccmail sends no mail for this project. Do not turn it back on: the user decides in the web app.

## Rules for long-running processes (only while notifications are ON)
They apply only when you recognise a long process: a background command, a training run, a pipeline or a wait you
expect to exceed about ten minutes. Nothing changes for ordinary tasks.

- **Who does the periodic checks: `status` says so.** Read the "periodic check of background processes" line:
  - **hook** (default): the daemon wakes you itself at intervals, only while background processes exist. A system
    reminder starting with `[ccmail]` arrives. **Do not schedule `ScheduleWakeup` yourself.** When it arrives, query the
    status (usually one call: task output or last log lines) and answer as described below.
  - **skill**: you schedule the period (`every N min`) with `ScheduleWakeup` at N·60 seconds, passing the same prompt.
    Do not schedule wait-only wake-ups when the system notifies you at the end of the process: leave a safety wake-up only.
  - **off**: no periodic check, neither by the daemon nor by you.
- If you cannot read `status`, use 30 minutes and skill mode.
- **On waking be as concise as possible, to save tokens.** Make the fewest calls (usually one, the cheapest command that
  gives the status). Do not re-read files or redo analysis. Write at most 3 lines: status (running, finished, failed),
  progress as a number, whether your intervention is needed. If nothing changed, say so in one line and reschedule.
- **When the process finishes or fails**, stop scheduling wake-ups and close with a summary of at most 10-15 lines:
  outcome, key figures, errors or anomalies, produced files, what to do next. That is the message ccmail uses as the
  body of the end-of-turn mail.
- **Confidential data.** In projects with company or personal data the summary contains no values from data rows:
  only outcome, counts and file names.

## General rules
- Never read or print credential files or stores: ccmail's `smtp.cred` in its data folder, the `ccmail:smtp` entry of
  the system credential store, nor the old prototype's `~/.claude/email-notify/config.json`.
- Do not edit `settings.json`, ccmail's code or its configuration. `init`, `install-hooks`, `install-skill` and
  `migrate` are run by the user.
- If ccmail reports an error, report it without retrying in loops and without showing credentials.
