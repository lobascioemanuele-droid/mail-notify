"""Optional HTML alternative of a notification (`html_mail` setting; the plain-text part is never changed).

What it adds, within what Gmail renders (no script, no form, no external resource):
  * one-tap reply buttons (OFF / ON / PAUSE, and "write to Claude") that are `mailto:` links: they open a reply
    already addressed and filled in with the command and the reference token; the user presses Send. A button
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
INK, MUTED, LINE, CARD, BOX = "#202124", "#5f6368", "#dadce0", "#ffffff", "#f8f9fa"


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


def _button(href: str, label: str, bg: str) -> str:
    return (f'<a href="{_esc(href)}" style="display:inline-block;margin:0 8px 8px 0;padding:10px 18px;'
            f'background:{bg};color:#ffffff;text-decoration:none;border-radius:6px;font-weight:bold;'
            f'font-size:14px">{_esc(label)}</a>')


def _paragraphs(lines, size="14px") -> str:
    text = "\n".join(lines).strip()
    return (f'<div style="font-size:{size};line-height:1.5;color:{INK};white-space:pre-wrap">{_esc(text)}</div>'
            if text else "")


def render(subject: str, body: str, settings: dict, to: str, full_text: str = "") -> str:
    T = lambda it, en: pick(settings, it, en)  # noqa: E731
    ctx, sections = split_sections(body)
    m = REF_RE.search(body or "")
    ref = m.group(0) if m else None
    out = [f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;'
           f'background:{CARD};color:{INK}">',
           f'<div style="font-size:17px;font-weight:bold;padding:12px 0">{_esc(subject)}</div>']
    if ctx:
        out.append(f'<div style="font-size:13px;color:{MUTED};line-height:1.6;border-top:1px solid {LINE};'
                   f'padding:8px 0">{"<br>".join(_esc(x) for x in ctx)}</div>')
    for title, lines in sections:
        if ref and any(x.startswith("Rif: ccm-") for x in lines):
            continue  # the reply instructions are replaced by the buttons below
        out.append(f'<div style="border-top:1px solid {LINE};padding:10px 0">'
                   f'<div style="font-size:11px;letter-spacing:1px;color:{MUTED};font-weight:bold;'
                   f'margin-bottom:6px">{_esc(title)}</div>')
        main = "\n".join(lines).strip()
        mono = title.upper().startswith(("COMANDI", "SUGGESTED"))
        out.append(_paragraphs(lines, "13px") if mono else _paragraphs(lines))
        full = (full_text or "").strip()
        if title in OUTCOME_TITLES and full and len(full) > len(main) + 20:
            out.append(f'<div style="font-size:12px;color:{MUTED};margin:10px 0 4px">'
                       f'{_esc(T("Messaggio completo (scorri in questo riquadro)", "Full message (scroll inside this box)"))}'
                       f'</div><div style="max-height:260px;overflow-y:auto;overflow-x:hidden;background:{BOX};'
                       f'border:1px solid {LINE};border-radius:6px;padding:10px;font-size:13px;line-height:1.5;'
                       f'white-space:pre-wrap;word-wrap:break-word">{_esc(full[:MAX_FULL])}</div>')
        out.append("</div>")
    if ref and to:
        out.append(f'<div style="border-top:1px solid {LINE};padding:12px 0">'
                   f'<div style="font-size:11px;letter-spacing:1px;color:{MUTED};font-weight:bold;'
                   f'margin-bottom:8px">{_esc(T("RISPOSTA RAPIDA", "QUICK REPLY"))}</div>'
                   + _button(mailto(to, subject, "OFF", ref), T("OFF progetto", "OFF project"), "#c5221f")
                   + _button(mailto(to, subject, "ON", ref), T("ON progetto", "ON project"), "#188038")
                   + _button(mailto(to, subject, "PAUSA 60", ref), T("PAUSA 60 min", "PAUSE 60 min"), "#b06000"))
        if settings.get("inbound_to_claude"):
            out.append(_button(mailto(to, subject, "", ref), T("Scrivi a Claude", "Write to Claude"), "#1a73e8"))
        out.append(f'<div style="font-size:12px;color:{MUTED};margin-top:4px">'
                   + _esc(T("Si apre una risposta già compilata: premi Invia.",
                            "A pre-filled reply opens: press Send.")) + f' ({_esc(ref)})</div></div>')
    out.append("</div>")
    return "<!doctype html><html><body style=\"margin:0;padding:12px;background:#ffffff\">" + "".join(out) + "</body></html>"
