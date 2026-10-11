"""Instant acknowledgement mail for an accepted e-mail reply: what was received, where it is queued and an
indicative time. The estimate comes from the observed length of earlier turns of the same chat (median of
the last ones) and from the session's state; it is an indication, not a promise."""
import json
import statistics

from . import render
from .heartbeat import wake_message  # noqa: F401  (kept importable for symmetry with the delivery path)

LISTEN_PERIOD_S = 3 * 3600  # keep in step with server.LISTEN_PERIOD_S: how long an idle chat is listening


def _fmt(sec: float) -> str:
    sec = max(0, int(sec))
    if sec < 90:
        return "meno di 2 minuti" if sec >= 30 else "pochi secondi"
    m = round(sec / 60)
    if m < 60:
        return f"circa {m} min"
    h, rem = divmod(m, 60)
    return f"circa {h} h" + (f" {rem:02d} min" if rem else "")


def typical_turn_s(db, session_id, project_dir=None):
    """Median duration of the last completed turns of this chat (or project), or None if unknown."""
    def durations(rows):
        out, start = [], None
        for r in rows:
            if r["event"] == "UserPromptSubmit":
                start = r["received_at"]
            elif r["event"] == "Stop" and start is not None:
                d = r["received_at"] - start
                if 5 <= d <= 4 * 3600:
                    out.append(d)
                start = None
        return out

    rows = db.q("SELECT event, received_at FROM events WHERE session_id=? AND event IN ('UserPromptSubmit','Stop') "
                "ORDER BY id DESC LIMIT 60", (session_id,))
    d = durations(list(reversed(rows)))[-8:]
    if len(d) < 2 and project_dir:
        rows = db.q("SELECT e.event, e.received_at FROM events e JOIN sessions s ON s.session_id=e.session_id "
                    "WHERE s.project_dir=? AND e.event IN ('UserPromptSubmit','Stop') ORDER BY e.id DESC LIMIT 80",
                    (project_dir,))
        d = durations(list(reversed(rows)))[-8:]
    return statistics.median(d) if d else None


def eta(db, session_id, now, parked=None) -> dict:
    """{'state': str, 'when': str, 'typical': str|None} describing where a message for `session_id` will land.

    `parked`: True/False if the daemon knows whether that chat's wake hook is really waiting (so a delivery
    "within seconds" is only promised when it is true); None when unknown."""
    s = db.one("SELECT * FROM sessions WHERE session_id=?", (session_id,))
    typical = typical_turn_s(db, session_id, (s or {}).get("project_dir"))
    typ = f"Di solito i turni di questa chat durano {_fmt(typical)}." if typical else None
    if not s:
        return {"state": "sessione non più nota", "typical": typ,
                "when": "Resta in coda (fino a 24 ore) e arriva al prossimo evento di quella chat."}
    state, last = s.get("state"), s.get("last_event_at") or now
    if state == "ended":
        return {"state": "la sessione è chiusa", "typical": typ,
                "when": "Resta in coda (fino a 24 ore) e arriva quando la chat riparte."}
    if state == "active":
        started = s.get("turn_start_at") or last
        el = max(0, now - started)
        # in-turn delivery: it rides on Claude's next tool call, it does not wait for the end of the turn
        when = ("Claude lo legge al suo prossimo passo (dopo il prossimo strumento che usa): di solito entro pochi "
                "secondi; se sta eseguendo un comando lungo, quando quel comando finisce.")
        if typical and el >= typical:
            when += " Il turno è già più lungo del solito."
        return {"state": f"Claude sta lavorando da {_fmt(el)}", "when": when, "typical": typ}
    # idle: the wake hook is parked for it
    if now - last > LISTEN_PERIOD_S:
        return {"state": f"la chat è ferma da {_fmt(now - last)}", "typical": typ,
                "when": "Arriva al prossimo evento della chat (la sua attesa automatica era già scaduta)."}
    st = "Claude è in attesa di processi in background" if s.get("bg_tasks") else "Claude è fermo"
    if parked is False:
        # honest: the message cannot be pushed because nothing is waiting for it in that chat
        return {"state": st + ", ma la chat non è in ascolto", "typical": typ,
                "when": ("Resta in coda e arriva al prossimo evento di quella chat (appena scrivi lì o Claude si "
                         "ferma). Il suo hook di risveglio non risulta attivo: succede se la chat è stata aperta "
                         "prima dell'installazione degli hook o dopo un riavvio del demone.")}
    if parked is None:
        return {"state": st, "typical": typ,
                "when": "Consegna immediata se la chat è in ascolto; altrimenti al prossimo evento della chat."}
    return {"state": st + " e in ascolto", "typical": typ,
            "when": "Consegnato entro pochi secondi: Claude riparte da solo e poi risponde."}


def build_claude_ack(db, settings, session_id, text, now, project="", chat_title="", parked=None, ref=None):
    e = eta(db, session_id, now, parked)
    lines = render.header(project, settings, session_id, chat_title)
    parts = ["\n".join(lines),
             render._section("Ricevuto", "La tua risposta è stata verificata e messa in coda per Claude:",
                             "  «" + render.clean_line(text, 160) + "»"),
             render._section("Quando arriva", f"Stato: {e['state']}.", e["when"])]
    if e["typical"]:
        parts[-1] += "\n" + e["typical"]
    parts.append(render._section("Poi", "Quando Claude finisce, ricevi la sua email di fine turno (se supera le "
                                        "soglie impostate)."))
    if ref:  # replying to this confirmation works exactly like replying to the original notification
        parts.append(render.manage_section(ref, bool(settings.get("inbound_to_claude"))))
    return f"[Claude Code] {render.clean_line(project or '?', 60)}: risposta ricevuta e messa in coda", \
        "\n\n".join(parts) + "\n"


def build_command_ack(settings, command: str, project_name: str, detail: str, ref=None):
    revert = {"off": "Per riaccenderlo rispondi ON a una email di ccmail, oppure usa la webapp.",
              "on": "Le notifiche di questo progetto sono di nuovo attive.",
              "pause": "Passato il tempo indicato le notifiche riprendono da sole."}.get(command, "")
    parts = [render._section("Comando eseguito", detail, revert)]
    if ref:
        parts.append(render.manage_section(ref, bool(settings.get("inbound_to_claude"))))
    return f"[Claude Code] {render.clean_line(project_name or 'ccmail', 60)}: comando eseguito", \
        "\n\n".join(parts) + "\n"
