"""Subject/body construction. Output is plain text; all model-derived text is redacted and truncated."""
import os
import re
import socket

from . import identity
from . import suggest as suggest_mod
from .redact import redact
from .summarize import summarize

_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def clean_line(s: str, limit: int = 120) -> str:
    s = _CTRL.sub(" ", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:limit - 1] + "…" if len(s) > limit else s


def headline(text: str, limit: int = 90) -> str:
    """First line of the message if it fits whole; "" otherwise (a cut-off fragment makes a bad subject)."""
    for line in (text or "").splitlines():
        line = line.strip().lstrip("#*-> ").strip()
        if line:
            line = clean_line(redact(line), 10_000)
            if len(line) <= limit:
                return line
            first = re.split(r"(?<=[.!?:])\s", line, maxsplit=1)[0]
            return first if len(first) <= limit else ""
    return ""


def _fmt_dur(sec):
    if sec is None:
        return ""
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def account_label(settings: dict) -> str:
    """Who the account belongs to: the configured name, else the Claude account's display name,
    else the Windows user name."""
    name = clean_line(str(settings.get("account_name") or ""), 60)
    if name:
        return name
    name = clean_line(identity.claude_display_name(), 60)
    if name:
        return name
    return clean_line(os.environ.get("USERNAME") or os.environ.get("USER") or socket.gethostname(), 60)


def header(project: str, settings: dict, session_id, chat_title: str = "") -> list:
    """The identifying lines every mail starts with: project, account name and the chat's name as shown in
    the desktop app (falls back to the short session id when the chat is not known)."""
    title = clean_line(chat_title or "", 100)
    who = f"Chat: {title}" if title else f"Sessione: {clean_line(str(session_id or '?'))[:8]}"
    return [f"Progetto: {clean_line(project or '?', 60)}", f"Account: {account_label(settings)}", who]


def build_watchdog(settings: dict, project: str, session_id, idle_minutes: int, n_bg: int, task_ids,
                   chat_title: str = "", ref: str = None) -> tuple:
    """Mail for a session that has been silent for a long time while background tasks were running."""
    project_c = clean_line(project or "?", 60)
    lines = header(project, settings, session_id, chat_title)
    lines += [f"Nessun evento da {idle_minutes} min, task in background dichiarati: {n_bg}"]
    if task_ids:
        lines.append("Task: " + ", ".join(clean_line(str(t), 20) for t in list(task_ids)[:5]))
    lines.append("")
    lines.append("La sessione sembra ferma: controlla se il processo è ancora in corso.")
    body = "\n".join(lines)
    if ref:
        body += "\n\n" + manage_section(ref, bool(settings.get("inbound_to_claude")))
    return f"[Claude Code] {project_c}: sessione ferma con task in background", body + "\n"


DIVIDER = "─" * 30


def _section(title: str, *lines) -> str:
    return "\n".join([DIVIDER, title.upper(), *lines])


def manage_section(ref: str, to_claude: bool = False) -> str:
    """The 'reply to this mail' block. EVERY mail ccmail sends (notifications, confirmations, watchdog) carries
    one, so replying to whichever is the latest works: the reference ties the reply to its project/session."""
    lines = ["Rispondi a questa email scrivendo, nella prima riga:",
             "  OFF    disattiva le notifiche di questo progetto",
             "  ON     le riattiva",
             "  PAUSA 60   silenzio per 60 minuti su tutti i progetti"]
    if to_claude:
        lines.append("Qualsiasi altro testo viene consegnato a Claude in questa chat (appena si ferma).")
    lines.append(f"Rif: {ref}")
    return _section("Gestione da questa email", *lines)


def _cut_whole(text: str, limit: int) -> str:
    """Truncate at a paragraph or sentence boundary, saying so, instead of in the middle of a sentence."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for sep in ("\n\n", "\n", ". ", "! ", "? "):
        i = head.rfind(sep)
        if i > limit * 0.5:
            head = head[: i + (0 if sep.startswith("\n") else 1)]
            break
    else:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip() + f"\n[… messaggio completo: {len(text)} caratteri, qui ne mostro {len(head)}]"


def build(event: str, payload: dict, settings: dict, project: str, elapsed, kind=None, n_bg=0,
          chat_title: str = "", limit_hit: bool = False, ref: str = None, inbound: bool = False,
          to_claude: bool = False):
    """Return (subject, body). kind: None | 'bg_done' | 'bg_status'; limit_hit: usage limit reached.

    The body is split into labelled sections: context, outcome, background processes, suggested
    commands and (when the reply channel is on) management instructions."""
    project = clean_line(project or "?", 60)
    mode = settings["content_mode"]  # summary | full | title | minimal (no model-derived text at all)
    full = mode in ("full", "summary")
    minimal = mode == "minimal"
    limit = settings["max_body_chars"]

    def shape(text):
        if mode == "summary":
            return summarize(text, settings.get("summary_chars", 600))
        return _cut_whole(text, limit)

    ntype = payload.get("notification_type") or ""
    tasks = payload.get("background_tasks") or []
    suggest_msg = ""

    if limit_hit:
        raw_msg = redact(str(payload.get("last_assistant_message") or payload.get("message") or ""))
        suggest_msg = raw_msg
        hint = suggest_mod.reset_hint(raw_msg)
        subject = f"[Claude Code] {project}: limite di utilizzo raggiunto" + (f" (si resetta {hint})" if hint else "")
        main = _cut_whole(raw_msg, 500) if not minimal else ""  # a limit notice is short: never reduced to a stub
    elif event == "Stop":
        msg = redact(str(payload.get("last_assistant_message") or ""))
        suggest_msg = msg
        if kind == "bg_done":
            subject = f"[Claude Code] {project}: processi in background terminati"
        elif kind == "bg_status":
            subject = f"[Claude Code] {project}: stato processi in background ({n_bg} attivi)"
        else:
            subject = (f"[Claude Code] {project}: "
                       f"{'task completato' if minimal else headline(msg) or 'task completato'}")
        main = shape(msg) if full else ""
    elif event == "Notification":
        title = "" if minimal else clean_line(str(payload.get("title") or ""), 60)
        subject = f"[Claude Code] {project}: serve il tuo input" + (f" ({title})" if title else "")
        msg = redact(str(payload.get("message") or ""))
        main = shape(msg) if full else ""
    elif event == "StopFailure":
        err = clean_line(str(payload.get("error") or "unknown"), 40)
        subject = f"[Claude Code] {project}: errore API ({err})"
        msg = redact(str(payload.get("last_assistant_message") or ""))
        main = shape(msg) if full else ""
    else:  # SessionEnd
        subject = f"[Claude Code] {project}: sessione terminata"
        main = ""

    ctx = header(project, settings, payload.get("session_id"), chat_title)
    if elapsed is not None and event == "Stop":
        ctx.append(f"Durata turno: {_fmt_dur(elapsed)}")
    if ntype:
        ctx.append(f"Tipo: {clean_line(ntype, 40)}")
    parts = ["\n".join(ctx)]
    if main:
        parts.append(_section("Esito", main))
    if tasks:
        ids = ", ".join(clean_line(str(t.get("id", "?")), 20) for t in tasks[:5] if isinstance(t, dict))
        parts.append(_section("Processi in background", f"Ancora attivi: {len(tasks)}" + (f" ({ids})" if ids else "")))
    if settings.get("suggestions_enabled", True) and event != "SessionEnd":
        sug = suggest_mod.suggest(event, kind, suggest_msg, ntype, len(tasks), limit_hit)
        if sug:
            parts.append(_section("Comandi suggeriti", "Incolla uno di questi nella chat:",
                                  *[f"  {i}. {s}" for i, s in enumerate(sug, 1)]))
    if inbound and ref:
        parts.append(manage_section(ref, to_claude))
    return clean_line(subject, 150), "\n\n".join(parts) + "\n"
