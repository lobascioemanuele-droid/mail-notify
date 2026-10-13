# Security

## Reporting
Use GitHub's private vulnerability reporting: **Security > Report a vulnerability** on the repository. Please don't file public issues for vulnerabilities. Fixes ship as a patch release with a CHANGELOG note.

## Supported versions
Only the latest release receives security fixes.

## Threat model

| Threat | Mitigation |
|---|---|
| Malicious web page calling the local API (CSRF, DNS rebinding) | Bind `127.0.0.1` only; `Host` must be `127.0.0.1:<port>`/`localhost:<port>`; foreign `Origin` rejected; no CORS; web session cookie is `HttpOnly; SameSite=Strict`, writes also need an `X-CSRF` header; JSON content-type enforced; strict CSP. |
| Other local process abusing the daemon | API needs a random token stored in a user-only file; the web login uses a 60 s single-use nonce obtained with that token; per-session cooldown and an hourly cap bound the damage. |
| Credential theft | Windows Credential Manager, macOS Keychain or Linux Secret Service; the password reaches `security` / `secret-tool` through stdin, never on a command line. Fallback file: DPAPI + ACL on Windows, mode 0600 *unencrypted* on macOS/Linux (announced loudly). No API returns or accepts the password; never logged; error messages are sanitised. |
| Secrets in mail content | Best-effort regex redaction (keys, tokens, `password=`…), truncation, "title only" mode. Not a guarantee — assume mail content is visible to your provider. |
| Header injection | Control characters stripped from subjects; messages built with `email.message.EmailMessage`. |
| SMTP interception | TLS with certificate verification (`ssl.create_default_context()`); no plaintext fallback. |
| Supply chain | Zero runtime dependencies. Install from the repo or PyPI only. |
| Hook breaking Claude Code | Hook client always exits 0, 3 s network timeout, spool fallback. |
| Prompt-injected model output or mail text triggering actions | Mail text is never executed. By default ccmail only sends mail. Replies can change the notification switches only through the command words (see *Reply channel*); free text reaches Claude only with `inbound_to_claude` on, under the checks in *Delivering replies to Claude*. |

## Out of scope
Malware running as your operating-system user (it can read the same stores as ccmail), and compromise of the mail provider account.

## Reply channel (v0.4, off by default)

When enabled, ccmail reads unread replies to its own mails over IMAP and accepts only a command word on the first
line: `OFF`, `ON`, or `PAUSA` / `PAUSE` / `SNOOZE` / `SLEEP` followed by optional minutes (1 to 1440, default 60), and
the Italian synonyms `SPEGNI`, `DISATTIVA`, `STOP`, `ACCENDI`, `ATTIVA`, `RIATTIVA`. A change happens only if **all**
hold: the `From` address is the configured recipient, the mail is vouched for (on Gmail, the Sent label of this
account, or the receiving server reports `dkim=pass` or `spf=pass`), the mail carries a `ccm-<12 hex>` reference
that this daemon issued in the last 14 days, and the message was not processed before. The effect is limited to a
per-project switch or a global pause. Mail text is never executed and never sent to Claude. Residual risk: whoever
controls the recipient's mailbox can switch notifications off (it cannot read or send anything else).

### Delivering replies to Claude (v0.5, separate switch, off by default)

With `inbound_to_claude` on, a free-text reply (one that does not start with a command word) is delivered into the Claude Code chat of the
session that sent the mail, through the wake hook, as a message of the user. This is more powerful than the three
commands because it is an instruction channel into an agent. It is guarded by the same checks (sender, Gmail Sent
label or DKIM/SPF, issued reference not older than 14 days, no replay, switch-on window) plus: only genuine replies
(`In-Reply-To` / `Re:`), never ccmail's own notifications; quoted text and the reference are stripped; length capped
at 2000 characters; pending messages expire after 24 h. Claude still acts under its normal permission mode, so
permission prompts apply. Residual risk: whoever can send mail as you *and* make Gmail label it as sent by your
account (i.e. controls your mailbox) can instruct Claude; keep the switch off if that is not acceptable.
