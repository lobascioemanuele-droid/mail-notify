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


def suggest(event: str, kind, message: str = "", ntype: str = "", n_bg: int = 0, limit_hit: bool = False) -> list:
    msg = message or ""
    if limit_hit:
        hint = reset_hint(msg)
        return [f"Riprendi da dove eri quando il limite si resetta{f' ({hint})' if hint else ''} e dimmi cosa resta.",
                "Salva lo stato del lavoro e i prossimi passi in un file di appunti prima di fermarti."]
    if event == "StopFailure":
        err = _error_line(msg)
        return [f"Riprova l'ultima operazione e dimmi l'esito{f' (errore: «{err}»)' if err else ''}.",
                "Spiegami in 3 righe cosa è andato storto e cosa cambieresti."]
    if event == "Notification":
        if ntype == "permission_prompt":
            what = _short(msg, 80)
            return [f"Apri la chat e valuta la richiesta di permesso{f': «{what}»' if what else ''}.",
                    "Se il permesso non serve, rispondi «no» e proponi un'alternativa che non lo richieda."]
        return ["Apri la chat: Claude è fermo in attesa di una tua risposta.",
                "Rispondi con una scelta netta così può proseguire senza altre domande."]

    files, err = _files(msg), _error_line(msg)
    tests = _TESTS.search(msg)
    prog = _PROGRESS.search(msg)
    question = _last_question(msg)

    if kind == "bg_done":
        out = ["Riassumi l'esito dei processi terminati: cifre chiave, anomalie e file prodotti (10-15 righe)."]
        if err:
            out.append(f"Analizza l'anomalia «{err}» e dimmi se invalida i risultati.")
        elif files:
            out.append(f"Verifica che «{files[0]}» sia coerente con l'esito e segnala cosa non torna.")
        else:
            out.append("Controlla che i risultati siano coerenti e segnala cosa non torna.")
        if tests:
            out.append("Rilancia i test e mostrami solo quelli falliti.")
        elif _DOC.search(msg):
            out.append("Aggiorna la documentazione con l'esito appena ottenuto.")
        return _dedupe(out)
    if kind == "bg_status":
        where = ""
        if prog:
            where = (f" (siamo al {prog.group(1)}%)" if prog.group(1) else
                     f" (fold {prog.group(2)} di {prog.group(3)})" if prog.group(2) else
                     f" ({prog.group(4)}/{prog.group(5)})")
        return [f"Continua ad aspettare{where} e avvisami solo se cambia qualcosa.",
                "Interrompi il processo e dimmi lo stato raggiunto, con cosa è stato salvato."]

    out = []
    if question:
        out += [f"Sì, procedi con quanto proposto: «{_short(question, 90)}»",
                "No, fermati qui e dimmi le alternative con pro e contro."]
    if err:
        out.append(f"Diagnostica «{err}», correggi e rilancia.")
    if _PLAN.search(msg) and not question:
        out.append("Procedi con la prima opzione e fermati prima di modifiche irreversibili.")
    if tests:
        done, total = tests.group(1), tests.group(2)
        out.append(f"Rilancia i test e conferma che {'tutti i ' + total if total else done} passano ancora.")
    if files:
        out.append(f"Mostrami le modifiche a «{files[0]}»" + (f" e «{files[1]}»." if len(files) > 1 else "."))
    if _PR.search(msg) and not question:
        out.append("Prepara il testo della PR (titolo, descrizione, come provarla) senza aprirla.")
    steps = _STEPS.findall(msg)
    if steps and not question:
        out.append("Procedi con lo step successivo e fermati se trovi un dubbio.")
    if _DOC.search(msg):
        out.append("Aggiorna la documentazione con quanto appena fatto.")
    out += ["Riassumi cosa hai fatto in 5 righe.", "Esegui i test e dammi solo l'esito."]
    return _dedupe(out)
