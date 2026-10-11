# Security

## Reporting
Open a private security advisory on the repository, or email the maintainer. Please don't file public issues for vulnerabilities.

## Threat model (v0.1, outbound-only)

| Threat | Mitigation |
|---|---|
| Malicious web page calling the local API (CSRF, DNS rebinding) | Bind `127.0.0.1` only; `Host` must be `127.0.0.1:<port>`/`localhost:<port>`; foreign `Origin` rejected; no CORS; web session cookie is `HttpOnly; SameSite=Strict`, writes also need an `X-CSRF` header; JSON content-type enforced; strict CSP. |
| Other local process abusing the daemon | API needs a random token stored in a user-only file; the web login uses a 60 s single-use nonce obtained with that token; per-session cooldown and an hourly cap bound the damage. |
| Credential theft | Credential Manager / DPAPI; no API returns or accepts the password; never logged; error messages are sanitised. |
| Secrets in mail content | Best-effort regex redaction (keys, tokens, `password=`…), truncation, "title only" mode. Not a guarantee — assume mail content is visible to your provider. |
| Header injection | Control characters stripped from subjects; messages built with `email.message.EmailMessage`. |
| SMTP interception | TLS with certificate verification (`ssl.create_default_context()`); no plaintext fallback. |
| Supply chain | Zero runtime dependencies. Install from the repo or PyPI only. |
| Hook breaking Claude Code | Hook client always exits 0, 3 s network timeout, spool fallback. |
| Prompt-injected model output triggering actions | v0.1 only *sends* mail; nothing in an email or event is ever executed. An inbound channel (if ever added) needs its own design (allow-lists, signing). |

## Out of scope
Malware running as your Windows user (it can read the same stores as ccmail), and compromise of the mail provider account.

## Reply channel (v0.4, off by default)

When enabled, ccmail reads unread replies to its own mails over IMAP and accepts only `OFF`, `ON` and
`PAUSA [min]` on the first line. A change happens only if **all** hold: the `From` address is the configured
recipient, the receiving server reports `dkim=pass` or `spf=pass`, the mail carries a `ccm-<12 hex>` reference
that this daemon issued in the last 14 days, and the message was not processed before. The effect is limited to a
per-project switch or a global pause. Mail text is never executed and never sent to Claude. Residual risk: whoever
controls the recipient's mailbox can switch notifications off (it cannot read or send anything else).

### Delivering replies to Claude (v0.5, separate switch, off by default)

With `inbound_to_claude` on, a free-text reply (not OFF/ON/PAUSA) is delivered into the Claude Code chat of the
session that sent the mail, through the wake hook, as a message of the user. This is more powerful than the three
commands because it is an instruction channel into an agent. It is guarded by the same checks (sender, Gmail Sent
label or DKIM/SPF, issued reference not older than 14 days, no replay, switch-on window) plus: only genuine replies
(`In-Reply-To` / `Re:`), never ccmail's own notifications; quoted text and the reference are stripped; length capped
at 2000 characters; pending messages expire after 24 h. Claude still acts under its normal permission mode, so
permission prompts apply. Residual risk: whoever can send mail as you *and* make Gmail label it as sent by your
account (i.e. controls your mailbox) can instruct Claude; keep the switch off if that is not acceptable.
