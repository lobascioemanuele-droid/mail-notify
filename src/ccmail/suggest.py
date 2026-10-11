"""Suggested replies to paste into the chat, built from the kind of mail AND from what the message says.

They are plain suggestions: nothing is sent to Claude and nothing is executed by ccmail. Everything is derived
locally with simple, deterministic rules (no model, no network).
"""
import re

_QUESTION_TAIL = re.compile(r"\?\s*$")
_ERROR_WORDS = re.compile(r"\b(errore|errori|error|fallit[oa]|fallimento|failed|failure|traceback|exception)\b", re.I)
_TESTS = re.compile(r"(\d[\d.]*)\s+test(?:\s+su\s+(\d[\d.]*))?", re.I)
_FILE = re.compile(r"(?<![\w/\\])((?:[\w.-]+[/\\])*[\w.-]+\.(?:py|js|ts|tsx|html|css|md|json|toml|yaml|yml|r|sql|csv|"
                   r"ipynb|txt|log))(?![\w])", re.I)
_PROGRESS = re.compile(r"(?:al|at)\s+(\d{1,3})\s?%|fold\s+(\d+)\s+di\s+(\d+)|(\d+)\s*/\s*(\d+)", re.I)
_PLAN = re.compile(r"\b(piano|plan|proposta|opzion[ei]|alternativ[ae]|scelta)\b", re.I)
_DOC = re.compile(r"\b(adr|documentazione|readme|report|relazione|changelog)\b", re.I)
_STEPS = re.compile(r"\b(step|fase|task|wp\d*)\s+([A-Z0-9]{1,3})\b", re.I)
_PR = re.compile(r"\b(pr|pull request)\b", re.I)


def is_usage_limit(text: str, error: str = "") -> bool:
    if error in ("rate_limit",):
        return True
    return bool(re.search(r"(hit|reached|raggiunto).{0,40}(limit|limite)|(session|usage|weekly) limit|"
                          r"limite di utilizzo|limite della sessione", text or "", re.I))


def reset_hint(text: str) -> str:
    m = re.search(r"resets?\s+(?:at\s+)?([0-9]{1,2}(?::[0-9]{2})?\s?(?:am|pm)?(?:\s*\([^)]{2,40}\))?)", text or "", re.I)
    return m.group(1).strip() if m else ""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`#>]+", "", s or "")).strip()


def _short(s: str, n: int = 70) -> str:
    s = _clean(s)
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def _last_question(msg: str) -> str:
    """The closing question of the message, if it ends with one (whole sentence, not cut)."""
    lines = [ln.strip() for ln in (msg or "").strip().splitlines() if ln.strip()]
    if not lines or not _QUESTION_TAIL.search(lines[-1]):
        return ""
    q = _clean(lines[-1])
    sents = re.split(r"(?<=[.!?])\s+", q)
    return sents[-1] if sents else q


def _files(msg: str, limit: int = 2) -> list:
    seen, out = set(), []
    for m in _FILE.finditer(msg or ""):
        f = m.group(1).strip(".,;:()")
        base = f.replace("\\", "/").split("/")[-1].lower()
        if len(base) > 3 and base not in seen:
            seen.add(base)
            out.append(f)
        if len(out) >= limit:
            break
    return out


def _error_line(msg: str) -> str:
    for ln in (msg or "").splitlines():
        if _ERROR_WORDS.search(ln) and len(_clean(ln)) > 12:
            return _short(ln, 80)
    return ""


def _dedupe(items, n=3):
    seen, out = set(), []
    for s in items:
        k = s.lower()
        if s and k not in seen:
            seen.add(k)
            out.append(s)
    return out[:n]


def suggest(event: str, kind, message: str = "", ntype: str = "", n_bg: int = 0, limit_hit: bool = False,
            lang: str = "it") -> list:
    en = lang == "en"

    def T(it, eng):
        return eng if en else it

    msg = message or ""
    if limit_hit:
        hint = reset_hint(msg)
        h = f" ({hint})" if hint else ""
        return [T(f"Riprendi da dove eri quando il limite si resetta{h} e dimmi cosa resta.",
                  f"Resume where you were when the limit resets{h} and tell me what is left."),
                T("Salva lo stato del lavoro e i prossimi passi in un file di appunti prima di fermarti.",
                  "Save the state of the work and the next steps in a notes file before stopping.")]
    if event == "StopFailure":
        err = _error_line(msg)
        return [T(f"Riprova l'ultima operazione e dimmi l'esito{f' (errore: «{err}»)' if err else ''}.",
                  f"Retry the last operation and tell me the result{f' (error: «{err}»)' if err else ''}."),
                T("Spiegami in 3 righe cosa è andato storto e cosa cambieresti.",
                  "Explain in 3 lines what went wrong and what you would change.")]
    if event == "Notification":
        if ntype == "permission_prompt":
            what = _short(msg, 80)
            return [T(f"Apri la chat e valuta la richiesta di permesso{f': «{what}»' if what else ''}.",
                      f"Open the chat and review the permission request{f': «{what}»' if what else ''}."),
                    T("Se il permesso non serve, rispondi «no» e proponi un'alternativa che non lo richieda.",
                      "If the permission is not needed, answer «no» and propose an alternative that does not need it.")]
        return [T("Apri la chat: Claude è fermo in attesa di una tua risposta.",
                  "Open the chat: Claude is waiting for your answer."),
                T("Rispondi con una scelta netta così può proseguire senza altre domande.",
                  "Answer with a clear choice so it can continue without further questions.")]

    files, err = _files(msg), _error_line(msg)
    tests = _TESTS.search(msg)
    prog = _PROGRESS.search(msg)
    question = _last_question(msg)

    if kind == "bg_done":
        out = [T("Riassumi l'esito dei processi terminati: cifre chiave, anomalie e file prodotti (10-15 righe).",
                 "Summarise the outcome of the finished processes: key figures, anomalies and produced files "
                 "(10-15 lines).")]
        if err:
            out.append(T(f"Analizza l'anomalia «{err}» e dimmi se invalida i risultati.",
                         f"Analyse the anomaly «{err}» and tell me whether it invalidates the results."))
        elif files:
            out.append(T(f"Verifica che «{files[0]}» sia coerente con l'esito e segnala cosa non torna.",
                         f"Check that «{files[0]}» is consistent with the outcome and flag what does not add up."))
        else:
            out.append(T("Controlla che i risultati siano coerenti e segnala cosa non torna.",
                         "Check that the results are consistent and flag what does not add up."))
        if tests:
            out.append(T("Rilancia i test e mostrami solo quelli falliti.", "Re-run the tests and show only the failures."))
        elif _DOC.search(msg):
            out.append(T("Aggiorna la documentazione con l'esito appena ottenuto.",
                         "Update the documentation with the result just obtained."))
        return _dedupe(out)
    if kind == "bg_status":
        where = ""
        if prog:
            if prog.group(1):
                where = T(f" (siamo al {prog.group(1)}%)", f" (we are at {prog.group(1)}%)")
            elif prog.group(2):
                where = T(f" (fold {prog.group(2)} di {prog.group(3)})", f" (fold {prog.group(2)} of {prog.group(3)})")
            else:
                where = f" ({prog.group(4)}/{prog.group(5)})"
        return [T(f"Continua ad aspettare{where} e avvisami solo se cambia qualcosa.",
                  f"Keep waiting{where} and tell me only if something changes."),
                T("Interrompi il processo e dimmi lo stato raggiunto, con cosa è stato salvato.",
                  "Stop the process and tell me the state reached and what was saved.")]

    out = []
    if question:
        out += [T(f"Sì, procedi con quanto proposto: «{_short(question, 90)}»",
                  f"Yes, go ahead with what you proposed: «{_short(question, 90)}»"),
                T("No, fermati qui e dimmi le alternative con pro e contro.",
                  "No, stop here and give me the alternatives with pros and cons.")]
    if err:
        out.append(T(f"Diagnostica «{err}», correggi e rilancia.", f"Diagnose «{err}», fix it and re-run."))
    if _PLAN.search(msg) and not question:
        out.append(T("Procedi con la prima opzione e fermati prima di modifiche irreversibili.",
                     "Go with the first option and stop before any irreversible change."))
    if tests:
        done, total = tests.group(1), tests.group(2)
        out.append(T(f"Rilancia i test e conferma che {'tutti i ' + total if total else done} passano ancora.",
                     f"Re-run the tests and confirm that {'all ' + total if total else done} still pass."))
    if files:
        out.append(T(f"Mostrami le modifiche a «{files[0]}»" + (f" e «{files[1]}»." if len(files) > 1 else "."),
                     f"Show me the changes to «{files[0]}»" + (f" and «{files[1]}»." if len(files) > 1 else ".")))
    if _PR.search(msg) and not question:
        out.append(T("Prepara il testo della PR (titolo, descrizione, come provarla) senza aprirla.",
                     "Prepare the PR text (title, description, how to test it) without opening it."))
    steps = _STEPS.findall(msg)
    if steps and not question:
        out.append(T("Procedi con lo step successivo e fermati se trovi un dubbio.",
                     "Go on with the next step and stop if you have a doubt."))
    if _DOC.search(msg):
        out.append(T("Aggiorna la documentazione con quanto appena fatto.", "Update the documentation with what was just done."))
    out += [T("Riassumi cosa hai fatto in 5 righe.", "Summarise what you did in 5 lines."),
            T("Esegui i test e dammi solo l'esito.", "Run the tests and give me only the result.")]
    return _dedupe(out)
