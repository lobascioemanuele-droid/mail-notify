"""Instant acknowledgement mail for an accepted e-mail reply: what was received, where it is queued and an
indicative time. The estimate comes from the observed length of earlier turns of the same chat (median of
the last ones) and from the session's state; it is an indication, not a promise."""
import statistics

from . import render
from .i18n import pick

LISTEN_PERIOD_S = 3 * 3600  # keep in step with server.LISTEN_PERIOD_S: how long an idle chat is listening


def _fmt(sec: float, settings=None) -> str:
    T = lambda it, en: pick(settings, it, en)  # noqa: E731
    sec = max(0, int(sec))
    if sec < 90:
        return T("meno di 2 minuti", "less than 2 minutes") if sec >= 30 else T("pochi secondi", "a few seconds")
    m = round(sec / 60)
    if m < 60:
        return T(f"circa {m} min", f"about {m} min")
    h, rem = divmod(m, 60)
    return T(f"circa {h} h", f"about {h} h") + (f" {rem:02d} min" if rem else "")


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


def eta(db, session_id, now, parked=None, settings=None) -> dict:
    """{'state': str, 'when': str, 'typical': str|None} describing where a message for `session_id` will land.

    `parked`: True/False if the daemon knows whether that chat's wake hook is really waiting (so a delivery
    "within seconds" is only promised when it is true); None when unknown."""
    T = lambda it, en: pick(settings, it, en)  # noqa: E731
    s = db.one("SELECT * FROM sessions WHERE session_id=?", (session_id,))
    typical = typical_turn_s(db, session_id, (s or {}).get("project_dir"))
    typ = (T(f"Di solito i turni di questa chat durano {_fmt(typical, settings)}.",
             f"Turns in this chat usually take {_fmt(typical, settings)}.") if typical else None)
    if not s:
        return {"state": T("sessione non più nota", "session no longer known"), "typical": typ,
                "when": T("Resta in coda (fino a 24 ore) e arriva al prossimo evento di quella chat.",
                          "It stays queued (up to 24 hours) and arrives at that chat's next event.")}
    state, last = s.get("state"), s.get("last_event_at") or now
    if state == "ended":
        return {"state": T("la sessione è chiusa", "the session is closed"), "typical": typ,
                "when": T("Resta in coda (fino a 24 ore) e arriva quando la chat riparte.",
                          "It stays queued (up to 24 hours) and arrives when the chat starts again.")}
    if state == "active":
        started = s.get("turn_start_at") or last
        el = max(0, now - started)
        # in-turn delivery: it rides on Claude's next tool call, it does not wait for the end of the turn
        when = T("Claude lo legge al suo prossimo passo (dopo il prossimo strumento che usa): di solito entro pochi "
                 "secondi; se sta eseguendo un comando lungo, quando quel comando finisce.",
                 "Claude reads it at its next step (after the next tool it uses): usually within a few seconds; "
                 "if it is running a long command, when that command ends.")
        if typical and el >= typical:
            when += T(" Il turno è già più lungo del solito.", " This turn is already longer than usual.")
        return {"state": T(f"Claude sta lavorando da {_fmt(el, settings)}",
                           f"Claude has been working for {_fmt(el, settings)}"), "when": when, "typical": typ}
    # idle: the wake hook is parked for it
    if now - last > LISTEN_PERIOD_S:
        return {"state": T(f"la chat è ferma da {_fmt(now - last, settings)}",
                           f"the chat has been idle for {_fmt(now - last, settings)}"), "typical": typ,
                "when": T("Arriva al prossimo evento della chat (la sua attesa automatica era già scaduta).",
                          "It arrives at the chat's next event (its automatic wait had already expired).")}
    st = (T("Claude è in attesa di processi in background", "Claude is waiting for background processes")
          if s.get("bg_tasks") else T("Claude è fermo", "Claude is idle"))
    if parked is False:
        # honest: the message cannot be pushed because nothing is waiting for it in that chat
        return {"state": st + T(", ma la chat non è in ascolto", ", but the chat is not listening"), "typical": typ,
                "when": T("Resta in coda e arriva al prossimo evento di quella chat (appena scrivi lì o Claude si "
                          "ferma). Il suo hook di risveglio non risulta attivo: succede se la chat è stata aperta "
                          "prima dell'installazione degli hook o dopo un riavvio del demone.",
                          "It stays queued and arrives at that chat's next event (as soon as you write there or "
                          "Claude stops). Its wake-up hook is not active: this happens when the chat was opened "
                          "before the hooks were installed or after a daemon restart.")}
    if parked is None:
        return {"state": st, "typical": typ,
                "when": T("Consegna immediata se la chat è in ascolto; altrimenti al prossimo evento della chat.",
                          "Immediate delivery if the chat is listening; otherwise at the chat's next event.")}
    return {"state": st + T(" e in ascolto", " and listening"),  "typical": typ,
            "when": T("Consegnato entro pochi secondi: Claude riparte da solo e poi risponde.",
                      "Delivered within a few seconds: Claude restarts by itself and then answers.")}


def build_claude_ack(db, settings, session_id, text, now, project="", chat_title="", parked=None, ref=None):
    T = lambda it, en: pick(settings, it, en)  # noqa: E731
    e = eta(db, session_id, now, parked, settings)
    lines = render.header(project, settings, session_id, chat_title)
    parts = ["\n".join(lines),
             render._section(T("Ricevuto", "Received"),
                             T("La tua risposta è stata verificata e messa in coda per Claude:",
                               "Your reply was verified and queued for Claude:"),
                             "  «" + render.clean_line(text, 160) + "»"),
             render._section(T("Quando arriva", "When it arrives"),
                             T(f"Stato: {e['state']}.", f"State: {e['state']}."), "", e["when"])]
    if e["typical"]:
        parts[-1] += "\n\n" + e["typical"]
    parts.append(render._section(T("Poi", "Next"),
                                 T("Quando Claude finisce, ricevi la sua email di fine turno (se supera le "
                                   "soglie impostate).",
                                   "When Claude finishes you receive its end-of-turn e-mail (if it passes the "
                                   "thresholds you set).")))
    if ref:  # replying to this confirmation works exactly like replying to the original notification
        parts.append(render.manage_section(ref, bool(settings.get("inbound_to_claude")), settings))
    return (T(f"[Claude Code] {render.clean_line(project or '?', 60)}: risposta ricevuta e messa in coda",
              f"[Claude Code] {render.clean_line(project or '?', 60)}: reply received and queued"),
            "\n\n".join(parts) + "\n")


def build_rejection(settings, reason: str, typed: str, project_name: str = "", ref=None):
    """Mail in the usual layout whose OUTCOME says that a verified reply was discarded, and why."""
    from .redact import redact
    T = lambda it, en: pick(settings, it, en)  # noqa: E731
    why, fix = {
        "no_session": (T("non so a quale chat inoltrarla: la email a cui hai risposto non è legata a una chat "
                         "(o la chat non c'è più).",
                         "I do not know which chat to forward it to: the e-mail you replied to is not tied to a "
                         "chat (or the chat no longer exists)."),
                       T("Rispondi a una notifica recente della chat, oppure scrivi direttamente in chat.",
                         "Reply to a recent notification of the chat, or write in the chat directly.")),
        "no_ref": (T("la email non contiene un riferimento «Rif: ccm-…» valido, oppure è più vecchio di 14 giorni.",
                     "the e-mail has no valid «Rif: ccm-…» reference, or it is older than 14 days."),
                   T("Rispondi a una email recente di ccmail senza cancellare la riga «Rif:».",
                     "Reply to a recent ccmail e-mail without deleting the «Rif:» line.")),
        "to_claude_off": (T("la consegna delle risposte a Claude è disattivata nelle impostazioni.",
                            "delivering replies to Claude is switched off in the settings."),
                          T("Attiva «Le risposte arrivano a Claude» nell'app web, oppure usa OFF / ON / PAUSA.",
                            "Turn on «Replies reach Claude» in the web app, or use OFF / ON / PAUSE.")),
    }.get(reason, (reason, ""))
    name = render.clean_line(project_name or "ccmail", 60)
    ctx = [T(f"Progetto: {name}", f"Project: {name}"), f"Account: {render.account_label(settings)}"]
    outcome = [T("Risposta scartata: ", "Reply discarded: ") + why]
    if typed:
        outcome += ["", T("Il tuo testo:", "Your text:"), "«" + render.clean_line(redact(typed), 300) + "»"]
    parts = ["\n".join(ctx), render._section(T("Esito", "Outcome"), *outcome),
             render._section(T("Cosa fare", "What to do"), fix)]
    if ref:
        parts.append(render.manage_section(ref, bool(settings.get("inbound_to_claude")), settings))
    return (T(f"[Claude Code] {name}: risposta scartata", f"[Claude Code] {name}: reply discarded"),
            "\n\n".join(parts) + "\n")


def build_command_ack(settings, command: str, project_name: str, detail: str, ref=None):
    T = lambda it, en: pick(settings, it, en)  # noqa: E731
    revert = {"off": T("Per riaccenderlo rispondi ON a una email di ccmail, oppure usa la webapp.",
                       "To turn it back on reply ON to a ccmail e-mail, or use the web app."),
              "on": T("Le notifiche di questo progetto sono di nuovo attive.",
                      "Notifications for this project are active again."),
              "pause": T("Passato il tempo indicato le notifiche riprendono da sole.",
                         "After the indicated time notifications resume by themselves.")}.get(command, "")
    name = render.clean_line(project_name or "ccmail", 60)
    parts = ["\n".join([T(f"Progetto: {name}", f"Project: {name}"), f"Account: {render.account_label(settings)}"]),
             render._section(T("Comando eseguito", "Command executed"), detail, "", revert)]
    if ref:
        parts.append(render.manage_section(ref, bool(settings.get("inbound_to_claude")), settings))
    return T(f"[Claude Code] {name}: comando eseguito", f"[Claude Code] {name}: command executed"), \
        "\n\n".join(parts) + "\n"
