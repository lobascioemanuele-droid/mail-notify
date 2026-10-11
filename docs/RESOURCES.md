# Resource consumption

What ccmail costs in tokens, CPU, memory, disk, network and latency, and how those numbers were obtained.

**Method.** Measured on Windows 11, Python 3.13 (Microsoft Store build), ccmail 0.6.0, an isolated daemon for the
synthetic runs and the author's real daemon for the idle runs. Token figures are *estimates* (no tokenizer was
available: characters ÷ 3.5–4, a range that covers Italian and English prose); network figures for IMAP are
estimates from protocol sizes, not socket counters. Everything marked **measured** comes from an actual run.
Reproduce with `tests/test_matrix.py` and the commands in [How to measure](#how-to-measure).

## Summary

| Resource | Cost | Basis |
|---|---|---|
| LLM tokens, idle / normal use | **0** (the daemon, hooks and web app never call a model) | by design, see below |
| LLM tokens, skill description | ≈ 55–65 per session, always | 221 characters, measured size |
| LLM tokens, `/mail-notify …` | ≈ 1,250–1,450 once per invocation | 4,954 characters, measured size |
| LLM tokens, one periodic wake-up | ≈ 130–150 of ccmail text + the model's own work (see below) | 519 characters, measured size |
| LLM tokens, an e-mail reply delivered to the chat | ≈ 65–75 + the reply (≤ ≈ 570) | 251 + ≤ 2,000 characters |
| Memory (daemon) | **≈ 33–38 MB** resident, ≈ 21 MB private, 6–10 threads | measured |
| CPU (daemon, idle, reply channel off) | **≈ 0.05 %** of one core | measured: 0.016 s / 30 s |
| CPU (daemon, idle, reply channel on) | **≈ 0.25 %** of one core | measured: 0.30 s / 120 s |
| CPU per hook call | ≈ 0.11 s (interpreter start-up) | measured |
| Latency added to Claude Code | ≈ 0.12 s per synchronous hook call | measured: median 117 ms, p90 134 ms |
| Disk | ≈ 0.2 MB database + ≤ 1.5 MB log; 30 days retention | measured |
| Network, SMTP | one TLS connection per mail, ≈ 6–10 KB | estimate |
| Network, IMAP (reply channel on) | ≈ 15 MB/day while it runs | estimate |
| Mail volume | ≤ `max_per_hour` (10) per hour; typical day: tens | measured: 37 mails on a heavy test day |

## 1. Tokens

### 1.1 Where tokens are **not** spent
- **The daemon, the hook client, the settings web app, SMTP and IMAP never contact a model.** They are ordinary
  processes. Hooks run outside the model loop and add nothing to the context unless they print `additionalContext`
  (only e-mail replies do).
- **Summaries are extractive and local** (`summarize.py`): sentence selection with rules, no model call. The
  suggested commands (`suggest.py`) are rule-based as well.
- **Mails never enter the context.** What is sent is derived from what Claude already produced.
- Hooks marked `async` (background `Bash` notices, the wake hook while parked) contribute nothing.

### 1.2 Where tokens **are** spent
Only text that ccmail places in front of the model counts. Sizes were measured on the actual strings.

| Path | Text reaching the model | Size | ≈ Tokens | How often |
|---|---|---|---|---|
| Skill listing | name + description | 221 characters | 55–65 | every session while the skill is installed |
| `/mail-notify on\|off\|status…` | `SKILL.md` body | 4,954 characters / 807 words | 1,250–1,450 | once per invocation; stays in that chat's context |
| Periodic wake-up (`heartbeat_mode = hook`) | reminder starting with `[ccmail]` | 519 chars (it) / 482 (en) | 130–150 | once per period **only while background tasks exist** |
| E-mail reply delivered to the chat | wrapper + your text | 251 / 231 chars + ≤ 2,000 | 65–75 + ≤ 570 | once per accepted reply (same-text duplicates are collapsed) |
| Claude Code's own framing of a hook message | a short fixed wrapper | — | ≈ 30–60 (estimate) | with each of the two paths above |

### 1.3 The real cost of a periodic wake-up
The reminder is small; **the dominant cost is re-reading the conversation**, which is true for *any* turn that
resumes an idle chat, not something ccmail adds on top of the work itself. Per wake-up the model:

1. reads the whole context `C` (cache read if still warm, otherwise a cache write at a higher rate);
2. issues one inexpensive tool call to read the task state (≈ 50–100 output tokens, result 100–1,000 tokens);
3. answers in ≤ 3 lines (≈ 60–90 output tokens) as the skill instructs.

So: **tokens per wake ≈ 2 × C (context reads: one possibly cold, one warm) + ≈ 150 (reminder) + result + ≈ 150
(output).** With a warm cache the 2 × C is billed at cache-read rates; if the period is longer than the cache
lifetime (5 minutes by default; some setups extend it) the first read is a cache write.

Worked example, `C` = 40,000 tokens, 30-minute period, an 8-hour job:

| Quantity | Value |
|---|---|
| wake-ups | 16 |
| context tokens read | 16 × 2 × 40,000 = 1.28 M (mostly cache reads) |
| new input per wake (reminder + result) | ≈ 250–1,150 |
| output per wake | ≈ 110–190 |
| ccmail's own text across the job | 16 × ≈ 150 = **≈ 2,400 tokens** |

Levers, from most to least effective: `heartbeat_mode = off` (zero wake-ups); a longer period
(`heartbeat_minutes` up to 240); `skill` mode makes Claude schedule the wake-ups itself, which costs the same
tokens but adds scheduling calls; keep the chat short (`C` is the multiplier). Wake-ups never happen without
background tasks, and never for a project whose switch is off.

### 1.4 E-mail replies
A reply is a user message. Delivered while the chat is idle it costs one ordinary turn (read `C`, act, answer).
Delivered mid-turn through the synchronous `PostToolBatch` hook it costs **no extra model call**: the text rides
on the next call that was going to happen anyway (≈ 65–75 tokens + the reply).

## 2. CPU

| Component | Measurement | Notes |
|---|---|---|
| Hook client (`python -m ccmail hook`) | CPU ≈ 109 ms, wall ≈ 117 ms median (p90 134, max 140; n = 22) | almost all of it is interpreter start-up and imports; the client is import-light by design |
| Daemon, reply channel **off** | 0.016 s of CPU in 30 s (≈ 0.05 %) | event-driven, sleeps on sockets and conditions |
| Daemon, reply channel **on** | 0.30 s in 120 s (≈ 0.25 %) | one persistent IMAP connection, a NOOP every 2 s, a full pass every `inbound_poll_s` (60 s) |
| Mail rendering / policy | far below the timer resolution | text processing of a few KB |

Per-call cost scales with Claude Code activity, not with time: a turn with 20 tool rounds fires
`UserPromptSubmit` + 20 × `PostToolBatch` + `Stop` ≈ 22 synchronous calls ≈ **2.6 s of added wall time spread
over the turn** and ≈ 2.4 s of CPU in short bursts.

## 3. Memory

| Component | Measured |
|---|---|
| Daemon | 33–38 MB working set, ≈ 21 MB private, 6–10 threads (grows by threads while a wake hook is parked: one idle waiter per listening chat) |
| Hook client | short-lived Python process; typical Python start-up footprint (≈ 10–15 MB, not measured separately) for ≈ 0.1 s |
| Parked wake hooks | one small Python process per chat that is listening for background work or replies |

## 4. Disk

| Item | Size | Bound |
|---|---|---|
| `ccmail.db` (+ `-wal`, `-shm`) | ≈ 0.17 MB + ≈ 0.3 MB WAL after 236 events and 37 mails | rows older than 30 days are purged at start |
| `ccmail.log` | ≈ 22 KB measured | rotating, 512 KB × 3 files at most |
| `spool/` | empty unless the daemon is down | at most 200 small files |
| Credentials | one Credential Manager / Keychain / Secret Service entry (or a ≈ 0.3 KB fallback file) | — |
| Mail bodies | 650 characters on average, 1,318 at most | `max_body_chars` / `summary_chars` |

## 5. Network

| Traffic | When | Estimate |
|---|---|---|
| SMTP | one new TLS connection per mail (outbox worker) | ≈ 6–10 KB per mail (handshake dominates); at the default cap of 10/hour ≤ 100 KB/h |
| IMAP keep-alive | reply channel on: `NOOP` every 2 s | ≈ 100–150 bytes per round trip with TLS → ≈ 4–6 MB/day |
| IMAP scan | every `inbound_poll_s` (60 s), plus when the mailbox grows | one `SEARCH` (14-day window) + **one batched header `FETCH`** for up to 200 messages + full fetches only for unseen ones: ≈ 8–10 KB per pass → ≈ 12 MB/day |
| Anything else | — | none: no telemetry, no update check, no third-party hosts |

The batched header fetch (0.6.0) replaced one round trip per message; it cut round trips from ≈ 60 per pass to
one. CPU did not change measurably because it is dominated by the keep-alive loop.

To lower IMAP use: raise `inbound_poll_s` (up to 600), or switch the reply channel off (traffic drops to zero).

## 6. Latency seen by Claude Code and by you

| Hook | Blocking? | Added time |
|---|---|---|
| `UserPromptSubmit`, `Stop`, `Notification`, `StopFailure`, `SessionStart/End` | yes (timeout 10 s) | ≈ 0.12 s each |
| `PostToolBatch` | yes (timeout 5 s) — needed so replies can reach Claude mid-turn | ≈ 0.12 s per round of tool calls |
| `PostToolUse` (Bash only), wake hook | no (`async`) | 0 |

If the daemon is down a hook starts it (≈ 1–2 s once) and falls back to the spool; the hook always exits 0 and has a
3-second network timeout, so it cannot hang Claude Code.

Mail latency (not a resource, but the number people ask about): SMTP send 1–3 s; delivery to your own address by the
provider 4–25 s (measured, outside ccmail's control); reply detection ≤ 2 s; hand-over to the chat ≈ 0.2 s.

## 7. Mail volume

| Gate | Default |
|---|---|
| Minimum turn length | 120 s |
| Cooldown per session | 60 s |
| Hard cap | 10 mails/hour |
| Silence after on/off | 60 s |
| Confirmation mails for on/off/init | none |

Replies add exactly one confirmation mail each (switch off with `inbound_ack`); a reply that starts a Claude turn
adds one answer mail at its end ("risposta alla tua email"), subject to the project switch, snooze and the hourly cap.

## How to measure

```bash
# combinations of every mail kind × mode × language × reply options, and policy vs. dry-run agreement
python -W ignore -m unittest discover -s tests -p "test_matrix.py"

# daemon memory and CPU (Windows PowerShell; replace <pid> with the value in daemon.json)
powershell -Command "$p=Get-Process -Id <pid>; $p.WorkingSet64/1MB; $p.CPU"

# text sizes that reach the model
python -c "from ccmail import heartbeat, skillinstall; print(len(heartbeat.wake_message(30,['t1'],'en')), len(skillinstall.packaged_path().read_text(encoding='utf-8')))"
```

## Honest limits
- Token figures are estimates from character counts; real tokenisation differs by a few tens of percent.
- The wake-up cost depends on the conversation size and cache state, which ccmail cannot control; the formula in
  1.3 is the way to size it for your usage.
- Resident memory was measured on Windows; macOS and Linux were not measured.
- IMAP traffic is estimated from protocol sizes, not counted on the socket.
