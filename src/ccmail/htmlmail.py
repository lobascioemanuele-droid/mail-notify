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

from .i18n import pick
from .render import DIVIDER

REF_RE = re.compile(r"\bccm-[0-9a-f]{12}\b")
OUTCOME_TITLES = ("ESITO", "OUTCOME")
MAX_FULL = 6000
INK, MUTED, LINE, CARD, BOX = "#1f2933", "#6b7785", "#e3e8ee", "#ffffff", "#f5f7fa"
FONT = "Segoe UI,Roboto,Helvetica,Arial,sans-serif"

# (words found in the subject, accent colour, tint, label it, label en); first match wins
KINDS = (
    (("limite di utilizzo", "usage limit"), "#b45309", "#fef3c7", "LIMITE RAGGIUNTO", "LIMIT REACHED"),
    (("errore api", "api error"), "#b91c1c", "#fee2e2", "ERRORE", "ERROR"),
    (("serve il tuo input", "input is needed"), "#1d4ed8", "#dbeafe", "SERVE IL TUO INPUT", "INPUT NEEDED"),
    (("risposta alla tua email", "answer to your e-mail"), "#6d28d9", "#ede9fe", "RISPOSTA", "ANSWER"),
    (("sessione ferma", "session stalled"), "#b91c1c", "#fee2e2", "SESSIONE FERMA", "STALLED"),
    (("background",), "#475569", "#e2e8f0", "IN BACKGROUND", "BACKGROUND"),
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
    for b in blocks[1:]:
        lines = b.splitlines()
        if len(lines) >= 2 and lines[0] == DIVIDER:
            sections.append((lines[1].strip(), lines[2:]))
    return ctx, sections


def _kind(subject: str, T):
    low = (subject or "").lower()
    for words, accent, tint, it, en in KINDS:
        if any(w in low for w in words):
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
    return (f'<td width="33%" style="padding:0 4px"><a href="{_esc(href)}" style="display:block;text-decoration:none;'
            f'text-align:center;background:{tint};border:1px solid {accent};border-radius:12px;padding:12px 4px">'
            f'<div style="font-size:20px;line-height:24px;color:{accent};font-weight:bold">{glyph}</div>'
            f'<div style="font-size:14px;font-weight:700;color:{accent};margin-top:2px">{_esc(label)}</div>'
            f'<div style="font-size:11px;color:{MUTED};margin-top:2px">{_esc(caption)}</div></a></td>')


def _chip(href: str, label: str) -> str:
    return (f'<a href="{_esc(href)}" style="display:inline-block;margin:0 6px 6px 0;padding:5px 12px;'
            f'border:1px solid {LINE};border-radius:999px;color:{INK};text-decoration:none;font-size:12px;'
            f'background:{CARD}">{label}</a>')


def _label(text: str) -> str:
    return (f'<div style="font-size:11px;letter-spacing:1.2px;color:{MUTED};font-weight:700;margin:0 0 8px">'
            f'{_esc(text)}</div>')


def _paragraphs(lines, size="14.5px") -> str:
    text = "\n".join(lines).strip()
    return (f'<div style="font-size:{size};line-height:1.55;color:{INK};white-space:pre-wrap">{_esc(text)}</div>'
            if text else "")


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
            o.append(f'<div style="font-family:Consolas,Menlo,monospace;font-size:12.5px;line-height:1.7;'
                     f'color:{INK};white-space:pre-wrap;background:{BOX};border-radius:8px;padding:10px 12px">'
                     f'{_esc(main)}</div>')
        else:
            o.append(_paragraphs(lines))
        full = (full_text or "").strip()
        if sec_title in OUTCOME_TITLES and full and len(full) > len(main) + 20:
            o.append(f'<div style="font-size:12px;color:{MUTED};margin:14px 0 6px">&#8595; '
                     f'{_esc(T("Messaggio completo: scorri dentro il riquadro", "Full message: scroll inside the box"))}'
                     f' ({len(full)} {_esc(T("caratteri", "characters"))})</div>'
                     f'<div style="max-height:260px;overflow-y:auto;overflow-x:hidden;background:{BOX};'
                     f'border:1px solid {LINE};border-left:4px solid {accent};border-radius:8px;padding:12px 14px;'
                     f'font-size:13.5px;line-height:1.6;white-space:pre-wrap;word-wrap:break-word">'
                     f'{_esc(full[:MAX_FULL])}</div>')
        o.append("</div>")
    if ref and to:
        cards = (_card(mailto(to, subject, "OFF", ref), "&#10005;", T("Spegni", "Turn off"),
                       T("questo progetto", "this project"), "#b91c1c", "#fef2f2")
                 + _card(mailto(to, subject, "ON", ref), "&#10003;", T("Riaccendi", "Turn on"),
                         T("questo progetto", "this project"), "#15803d", "#f0fdf4")
                 + _card(mailto(to, subject, "PAUSA 60", ref), "&#10074;&#10074;", T("Pausa 1 ora", "Pause 1 hour"),
                         T("tutti i progetti", "all projects"), "#b45309", "#fffbeb"))
        extra = "".join(_chip(mailto(to, subject, f"PAUSA {n}", ref), _esc(lab))
                        for n, lab in ((15, "15 min"), (180, T("3 ore", "3 hours")), (480, T("8 ore", "8 hours"))))
        talk = (_chip(mailto(to, subject, "", ref),
                      "&#9998; " + _esc(T("Scrivi a Claude in questa chat", "Write to Claude in this chat")))
                if settings.get("inbound_to_claude") else "")
        o.append(f'<div style="margin:12px 0 0;padding:14px 14px 10px;border:1px solid {LINE};border-radius:12px;'
                 f'background:{CARD}"><div style="padding:0 4px">{_label(T("RISPOSTA RAPIDA", "QUICK REPLY"))}</div>'
                 f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                 f'style="border-collapse:collapse"><tr>{cards}</tr></table>'
                 f'<div style="padding:10px 4px 0">{talk}<span style="font-size:12px;color:{MUTED};margin-right:6px">'
                 f'{_esc(T("Altre pause:", "Other pauses:"))}</span>{extra}</div>'
                 f'<div style="padding:4px 4px 0;font-size:11px;color:{MUTED}">'
                 + _esc(T("Si apre una risposta già compilata: premi Invia.", "A pre-filled reply opens: press Send."))
                 + f' &middot; {_esc(ref)}</div></div>')
    o.append("</div>")
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '</head><body style="margin:0;padding:12px;background:#eef1f5">' + "".join(o) + "</body></html>")
