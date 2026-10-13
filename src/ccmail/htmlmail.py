"""Optional HTML alternative of a notification (`html_mail` setting; the plain-text part is never changed).

What it adds, within what Gmail renders (no script, no form, no external resource):
  * one-tap reply cards (OFF / ON / PAUSE, other pauses, "write to Claude") that are `mailto:` links: they open a
    reply already addressed and filled in with the command and the reference token; the user presses Send. A button
    that acts without that final Send needs Gmail "actions", which require registration with Google and a
    public HTTPS endpoint, so it cannot work with a daemon that listens on loopback only;
  * the OUTCOME section shown in full inside a box with its own scroll bar (CSS `overflow-y: auto`), so a long
    message does not stretch the mail.

The HTML is derived from the plain-text body that was queued, so both parts always agree.
"""
import html
import re
from urllib.parse import quote

from . import inbound
from .i18n import pick
from .render import DIVIDER

REF_RE = re.compile(r"\bccm-[0-9a-f]{12}\b")
OUTCOME_TITLES = ("ESITO", "OUTCOME")
MAX_FULL = 6000
INK, MUTED, LINE, CARD, BOX = "#1f2933", "#6b7785", "#e3e8ee", "#ffffff", "#f5f7fa"
FONT = "Segoe UI,Roboto,Helvetica,Arial,sans-serif"

# (words found in the subject, accent colour, tint, label it, label en); first match wins
KINDS = (
    (("risposta ricevuta e messa in coda", "reply received and queued"), "#1d4ed8", "#dbeafe", "RICEVUTA, IN CODA", "RECEIVED, QUEUED"),
    (("comando eseguito", "command executed"), "#15803d", "#dcfce7", "COMANDO ESEGUITO", "COMMAND EXECUTED"),
    (("risposta scartata", "reply discarded"), "#b91c1c", "#fee2e2", "RISPOSTA SCARTATA", "REPLY DISCARDED"),
    (("email di prova", "test e-mail"), "#475569", "#e2e8f0", "PROVA", "TEST"),
    (("limite di utilizzo", "usage limit"), "#b45309", "#fef3c7", "LIMITE RAGGIUNTO", "LIMIT REACHED"),
    (("errore api", "api error"), "#b91c1c", "#fee2e2", "ERRORE", "ERROR"),
    (("serve il tuo input", "your input is needed"), "#1d4ed8", "#dbeafe", "SERVE IL TUO INPUT", "INPUT NEEDED"),
    (("risposta alla tua email", "answer to your e-mail"), "#6d28d9", "#ede9fe", "RISPOSTA", "ANSWER"),
    (("sessione ferma", "session stalled"), "#b91c1c", "#fee2e2", "SESSIONE FERMA", "STALLED"),
    (("processi in background", "stato processi in background", "background processes"), "#475569", "#e2e8f0",
     "IN BACKGROUND", "BACKGROUND"),
    (("sessione terminata", "session ended"), "#475569", "#e2e8f0", "TERMINATA", "ENDED"),
)


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def mailto(to: str, subject: str, first_line: str, ref: str) -> str:
    body = f"{first_line}\n\nRif: {ref}" if first_line else f"\n\nRif: {ref}"
    return f"mailto:{quote(to, safe='@')}?subject={quote('Re: ' + subject, safe='')}&body={quote(body, safe='')}"


def split_sections(body: str):
    """-> (context lines, [(TITLE, [lines])]) from a ccmail plain-text body."""
    blocks = re.split(r"\n\n(?=" + re.escape(DIVIDER) + r"\n)", (body or "").strip("\n"))
    ctx = blocks[0].splitlines() if blocks else []
    sections = []
    rest = blocks[1:]
    if ctx and ctx[0] == DIVIDER:  # no context lines: the first block is already a section
        ctx, rest = [], blocks
    for b in rest:
        lines = b.splitlines()
        if len(lines) >= 2 and lines[0] == DIVIDER:
            sections.append((lines[1].strip(), lines[2:]))
    return ctx, sections


def _kind(subject: str, T):
    low = re.sub(r"^\[claude code\]\s*", "", (subject or "").lower())
    low = low.split(": ", 1)[1] if ": " in low else low  # drop the project name: "<project>: <what happened>"
    for words, accent, tint, it, en in KINDS:
        if any(low.startswith(w) for w in words):
            return accent, tint, T(it, en)
    return "#15803d", "#dcfce7", T("COMPLETATO", "DONE")


def _meta_grid(ctx) -> str:
    """'Label: value' lines as a two-column grid; lines without a label are shown as they are."""
    rows = []
    for line in ctx:
        k, sep, v = line.partition(": ")
        if sep and len(k) <= 20:
            rows.append(f'<tr><td style="padding:3px 14px 3px 0;color:{MUTED};font-size:12px;white-space:nowrap">'
                        f'{_esc(k)}</td><td style="padding:3px 0;font-size:13px;color:{INK};font-weight:600">'
                        f'{_esc(v)}</td></tr>')
        else:
            rows.append(f'<tr><td colspan="2" style="padding:3px 0;font-size:13px;color:{INK}">{_esc(line)}</td></tr>')
    return ('<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            + "".join(rows) + "</table>")


def _card(href: str, glyph: str, label: str, caption: str, accent: str, tint: str) -> str:
    """One of three equal, compact action cards: icon and name on one line, a short caption under it. Style sits
    on the <td> (table-layout: fixed in the parent), so the cells share one width and one height."""
    return (f'<td width="33%" valign="top" style="background:{tint};border:1px solid {accent};'
            f'border-radius:10px;text-align:center">'
            f'<a href="{_esc(href)}" style="display:block;text-decoration:none;padding:7px 2px">'
            f'<div style="font-size:14px;line-height:18px;font-weight:700;color:{accent}">{glyph} {_esc(label)}</div>'
            f'<div style="font-size:10.5px;line-height:13px;color:{MUTED}">{_esc(caption)}</div>'
            f'</a></td>')


def _chip(href: str, label: str) -> str:
    """A rounded pill filling its (equal-width) table cell."""
    return (f'<a href="{_esc(href)}" style="display:block;padding:5px 4px;border:1px solid {LINE};'
            f'border-radius:999px;color:{INK};text-decoration:none;font-size:13px;text-align:center;'
            f'background:{CARD}">{label}</a>')


def _label(text: str) -> str:
    return (f'<div style="font-size:11px;letter-spacing:1.2px;color:{MUTED};font-weight:700;margin:0 0 8px">'
            f'{_esc(text)}</div>')


def _paragraphs(lines, size="14.5px") -> str:
    text = "\n".join(lines).strip()
    return (f'<div style="font-size:{size};line-height:1.55;color:{INK};white-space:pre-wrap">{_esc(text)}</div>'
            if text else "")


_ITEM = re.compile(r"^\s*(\d+)\.\s+(.*\S)\s*$")


def _suggestions(lines, to: str, ref, subject: str, tappable: bool, T) -> str:
    intro, rows = [], []
    for ln in lines:
        m = _ITEM.match(ln)
        if m:
            rows.append((m.group(1), m.group(2)))
        elif ln.strip():
            intro.append(ln.strip())
    out = [f'<div style="font-size:13px;color:{MUTED};margin:0 0 10px">{_esc(" ".join(intro))}</div>'] if intro else []
    for num, text in rows:
        cell = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                f'style="border-collapse:collapse;table-layout:fixed"><tr>'
                f'<td width="26" valign="top" style="font-size:14px;line-height:1.5;font-weight:700;color:{INK}">'
                f'{_esc(num)}.</td>'
                f'<td valign="top" style="font-size:14px;line-height:1.5;color:{INK};word-wrap:break-word">'
                f'{_esc(text)}</td></tr></table>')
        if tappable and inbound.parse_command(text) is None:
            out.append(f'<a href="{_esc(mailto(to, subject, text, ref))}" style="display:block;margin:0 0 8px;'
                       f'padding:9px 11px;border:1px solid #000000;border-radius:0;background:#e9ecef;'
                       f'text-decoration:none;color:{INK}">{cell}</a>')
        else:
            out.append(f'<div style="margin:0 0 8px;padding:2px 0">{cell}</div>')
    if rows and tappable:
        out.append(f'<div style="font-size:11px;color:{MUTED};margin-top:2px">'
                   + _esc(T("Tocca un comando per mandarlo a Claude: si apre una risposta già compilata, premi Invia.",
                            "Tap a command to send it to Claude: a pre-filled reply opens, press Send.")) + "</div>")
    return "".join(out)


_FENCE_RE = re.compile(r"```[^\n`]*\n?(.*?)(?:```|\Z)", re.S)
CODE_BG = "#e9ecef"
MONO = "Consolas,Menlo,'Courier New',monospace"


def _spans(s: str) -> str:
    """Inline Markdown of one already-escaped line: `code` is kept verbatim, **bold** / __bold__ may contain code
    spans, *italic* is italic."""
    codes = []

    def keep(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`\n]+)`", keep, s)
    s = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda x: "<b>" + (x.group(1) or x.group(2)) + "</b>", s)
    s = re.sub(r"(?<![*\w])\*([^*\s][^*\n]*?)\*(?![*\w])", lambda x: "<i>" + x.group(1) + "</i>", s)
    return re.sub(r"\x00(\d+)\x00",
                  lambda m: (f'<span style="font-family:{MONO};background:{CODE_BG};border-radius:4px;'
                             f'padding:1px 5px;font-size:12.5px">{codes[int(m.group(1))]}</span>'), s)


def _inline(line: str) -> str:
    """One text line: headings, bullets and inline Markdown, escaped first."""
    s = _esc(line).replace("\x00", "")  # NUL is the placeholder delimiter of _spans: never let input carry it
    m = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", s)
    if m:
        return "<b>" + _spans(m.group(1)) + "</b>"
    s = re.sub(r"^(\s*)[-*]\s+", lambda x: x.group(1) + "• ", s)
    return _spans(s)


def md_to_html(text: str) -> str:
    """The small part of Markdown that a Claude answer uses, as safe HTML: fenced code as a code block, inline
    code, bold, headings and bullets. Everything is escaped first; the only tags are the ones written here."""
    out, pos = [], 0

    def prose(chunk: str):
        chunk = chunk.strip("\n")
        if chunk.strip():
            out.append('<div style="white-space:pre-wrap">' + "\n".join(_inline(x) for x in chunk.split("\n")) + "</div>")

    for m in _FENCE_RE.finditer(text or ""):
        prose(text[pos:m.start()])
        code = m.group(1).rstrip("\n")
        if code.strip():
            out.append(f'<div style="font-family:{MONO};font-size:12.5px;line-height:1.5;background:{CODE_BG};'
                       f'border:1px solid {LINE};border-radius:6px;padding:8px 10px;margin:8px 0;'
                       f'white-space:pre-wrap;word-wrap:break-word">{_esc(code)}</div>')
        pos = m.end()
    prose((text or "")[pos:])
    return "".join(out)


def strip_leading(full: str, shown: str) -> str:
    """The whole message without the sentences it starts with that the OUTCOME section already shows."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[*_`#>]+", "", s or "")).strip().lower()

    shown_n = norm(shown)
    parts = re.findall(r"[^.!?\n]+[.!?]*[ \t]*|\n+", full or "")
    i = 0
    while i < len(parts):
        p = parts[i]
        if not p.strip():
            i += 1
            continue
        n = norm(p)
        if len(n) >= 8 and n in shown_n:
            i += 1
            continue
        break
    return "".join(parts[i:]).strip()


def remainder(full: str, shown: str) -> str:
    """The part of the whole message that the OUTCOME section does not already show (sentence by sentence)."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[*_`#>]+", "", s or "")).strip().lower()

    shown_n = norm(re.sub(r"\[(…|\.\.\.)[^\]]*\]\s*$", "", shown or ""))
    parts = re.findall(r"[^.!?\n]+[.!?]*[ \t]*|\n+", full or "")
    keep = [x for x in parts if not (len(norm(x)) >= 8 and norm(x) in shown_n)]
    return re.sub(r"\n{3,}", "\n\n", "".join(keep)).strip()


def _quote(text: str, accent: str) -> str:
    return (f'<div style="margin:8px 0;padding:10px 12px;background:{BOX};border-left:4px solid {accent};'
            f'border-radius:6px;font-size:14px;line-height:1.5;color:{INK};white-space:pre-wrap;'
            f'word-wrap:break-word">{_esc(text)}</div>')


def _spaced(lines) -> str:
    """Each line as its own block with space between them; 'Label: value' gets a bold label."""
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        k, sep, v = s.partition(": ")
        inner = (f'<b>{_esc(k)}:</b> {_esc(v)}' if sep and 0 < len(k) <= 18 and v else _esc(s))
        out.append(f'<div style="font-size:14.5px;line-height:1.5;margin:0 0 9px;color:{INK}">{inner}</div>')
    return "".join(out)


def _body_lines(lines, accent: str, spaced: bool = False) -> str:
    """Plain lines as paragraphs; a line that is a quotation («…», as in confirmations) as a quote block."""
    out, buf = [], []

    def flush():
        if buf:
            out.append(_spaced(buf) if spaced else _paragraphs(buf))
            buf.clear()

    for ln in lines:
        s = ln.strip()
        quoted = s.startswith("«") and s.endswith("»")
        if quoted:
            flush()
            out.append(_quote(s, accent))
        else:
            buf.append(ln)
    flush()
    return "".join(out)


def render(subject: str, body: str, settings: dict, to: str, full_text: str = "") -> str:
    T = lambda it, en: pick(settings, it, en)  # noqa: E731
    ctx, sections = split_sections(body)
    m = REF_RE.search(body or "")
    ref = m.group(0) if m else None
    accent, tint, badge = _kind(subject, T)
    title = re.sub(r"^\[Claude Code\]\s*", "", subject or "")
    o = [f'<div style="font-family:{FONT};max-width:620px;margin:0 auto;color:{INK}">',
         f'<div style="background:{tint};border-left:6px solid {accent};border-radius:10px;padding:16px 18px">'
         f'<span style="display:inline-block;background:{accent};color:#ffffff;font-size:11px;font-weight:700;'
         f'letter-spacing:1.2px;padding:3px 10px;border-radius:999px">{_esc(badge)}</span>'
         f'<div style="font-size:18px;line-height:1.35;font-weight:700;margin-top:10px;color:{INK}">'
         f'{_esc(title)}</div></div>']
    if ctx:
        o.append(f'<div style="padding:14px 18px 6px">{_meta_grid(ctx)}</div>')
    for sec_title, lines in sections:
        if ref and any(x.startswith("Rif: ccm-") for x in lines):
            continue  # the reply instructions are replaced by the cards below
        main = "\n".join(lines).strip()
        o.append(f'<div style="margin:12px 0 0;padding:14px 18px;border:1px solid {LINE};border-radius:12px;'
                 f'background:{CARD}">{_label(sec_title)}')
        if sec_title.upper().startswith(("COMANDI", "SUGGESTED")):
            o.append(_suggestions(lines, to, ref, subject, bool(ref and to and settings.get("inbound_to_claude")), T))
        else:
            if sec_title in OUTCOME_TITLES and settings.get("content_mode") == "full":
                o.append(f'<div style="font-size:14.5px;line-height:1.55;color:{INK}">{md_to_html(main)}</div>')
            else:
                o.append(_body_lines(lines, accent, spaced=sec_title not in OUTCOME_TITLES))
        full = strip_leading((full_text or "").strip(), main) if sec_title in OUTCOME_TITLES else ""
        if len(full) >= 20:
            o.append(f'<div style="font-size:12px;color:{MUTED};margin:14px 0 6px">&#8595; '
                     f'{_esc(T("Messaggio completo", "Full message"))}'
                     f' ({len(full)} {_esc(T("caratteri", "characters"))})</div>'
                     f'<div style="max-height:340px;overflow-y:auto;overflow-x:hidden;background:{BOX};'
                     f'border:1px solid {LINE};border-left:4px solid {accent};border-radius:8px;padding:12px 14px;'
                     f'font-size:13.5px;line-height:1.6;word-wrap:break-word">'
                     f'{md_to_html(full[:MAX_FULL])}</div>')
        o.append("</div>")
    if ref and to:
        cards = (_card(mailto(to, subject, "OFF", ref), "&#10005;", "OFF",
                       T("questo progetto", "this one"), "#b91c1c", "#fef2f2")
                 + _card(mailto(to, subject, "ON", ref), "&#10003;", "ON",
                         T("questo progetto", "this one"), "#15803d", "#f0fdf4")
                 + _card(mailto(to, subject, "PAUSA 60", ref), "&#10074;&#10074;", "SLEEP",
                         T("1 ora, tutti", "1 hour, all"), "#b45309", "#fffbeb"))
        pauses = "".join(f'<td width="33%" valign="top" style="padding:0">{_chip(mailto(to, subject, f"PAUSA {n}", ref), _esc(lab))}</td>'
                         for n, lab in ((15, "15 min"), (180, T("3 ore", "3 hours")), (480, T("8 ore", "8 hours"))))
        talk = (f'<div style="margin:12px 0 0">{_chip(mailto(to, subject, "", ref), "&#9998; " + _esc(T("Scrivi a Claude in questa chat", "Write to Claude in this chat")))}</div>'
                if settings.get("inbound_to_claude") else "")
        table = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                 'style="border-collapse:separate;border-spacing:6px 0;table-layout:fixed">')
        o.append(f'<div style="margin:12px 0 0;padding:14px;border:1px solid {LINE};border-radius:12px;'
                 f'background:{CARD};text-align:center">{_label(T("RISPOSTA RAPIDA", "QUICK REPLY"))}'
                 f'{table}<tr>{cards}</tr></table>'
                 f'<div style="font-size:12px;color:{MUTED};margin:8px 0 4px">'
                 f'{_esc(T("Altre pause (tutti i progetti)", "Other pauses (all projects)"))}</div>'
                 f'{table}<tr>{pauses}</tr></table>{talk}'
                 f'<div style="margin-top:12px;font-size:11px;color:{MUTED}">'
                 + _esc(T("Si apre una risposta già compilata: premi Invia.", "A pre-filled reply opens: press Send."))
                 + f' &middot; {_esc(ref)}</div></div>')
    o.append("</div>")
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '</head><body style="margin:0;padding:12px;background:#eef1f5">' + "".join(o) + "</body></html>")
