"""Extractive summary of an assistant message (no model, no network): lead sentences, key list items,
and a trailing question if the message ends by asking something. Deterministic and local.

Rule: never cut a sentence in the middle. Whole items are kept or dropped ("(+N altri punti)"); a single
sentence is only shortened at a word boundary and marked with "[…]" when it is longer than the hard cap.
"""
import re

_FENCE = re.compile(r"```.*?(```|$)", re.S)
_MD = re.compile(r"(\*\*|__|`|^#{1,6}\s*|^>\s*)", re.M)
_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)")
_HEAD = re.compile(r"^\s*#{1,6}\s+(.*\S)")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ0-9\"'(\[])")
MAX_ITEMS = 6
ITEM_SOFT = 240   # an item longer than this is reduced to its first sentence
SENT_HARD = 320   # a single sentence longer than this is cut at a word boundary
LEAD_SENTENCES = 2


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _MD.sub("", s)).strip(" -–:")


def _sentences(s: str) -> list:
    return [p.strip() for p in _SENT.split(s) if p.strip()]


def _hard_cut(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + " […]"


def _item_text(s: str) -> str:
    s = _clean(s)
    if len(s) > ITEM_SOFT:
        s = _sentences(s)[0]  # first complete sentence of a long item
    return _hard_cut(s, SENT_HARD)


def summarize(text: str, limit: int = 600) -> str:
    text = _FENCE.sub("", text or "").strip()
    if not text:
        return ""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    items, heads, lead_src = [], [], None
    for p in paras:
        lines = [ln for ln in p.splitlines() if ln.strip()]
        plain = [ln for ln in lines if not _ITEM.match(ln) and not _HEAD.match(ln) and not ln.lstrip().startswith("|")]
        if lead_src is None and plain and len(plain) == len(lines):
            lead_src = " ".join(plain)
        for ln in lines:
            if ln.lstrip().startswith("|"):
                continue
            m = _HEAD.match(ln)
            target = heads if m else items
            m = m or _ITEM.match(ln)
            if m:
                c = _clean(m.group(1))
                if c:
                    target.append(c)
    items = items or heads  # headings are only a fallback: bullets carry the substance

    lead = ""
    if lead_src:
        sents = _sentences(_clean(lead_src))
        lead = " ".join(sents[:LEAD_SENTENCES]) if sents else ""
        if len(lead) > limit:  # keep at least one whole sentence
            lead = sents[0]
        lead = _hard_cut(lead, min(SENT_HARD, limit))
    question = ""
    last_lines = paras[-1].splitlines() if paras else []
    last = _clean(last_lines[-1]) if last_lines else ""
    if last.endswith("?") and last not in lead:
        question = _hard_cut(last, SENT_HARD)

    fixed = [x for x in (lead, question) if x]
    used = sum(len(x) + 1 for x in fixed)
    chosen = []
    for it in items[:MAX_ITEMS]:
        t = "• " + _item_text(it)
        if chosen and used + len(t) + 1 > limit - 20:  # room for the "(+N altri punti)" line
            break
        if not chosen and used + len(t) + 1 > limit and fixed:
            break
        chosen.append(t)
        used += len(t) + 1
    extra = len(items) - len(chosen)
    out = ([lead] if lead else []) + chosen
    if extra > 0:
        out.append(f"(+{extra} altri punti)")
    if question:
        out.append(question)
    if not out:
        out = [_hard_cut(_clean(text), min(limit, SENT_HARD))]
    res = "\n".join(out)
    if len(res) > limit:  # a configured cap smaller than one sentence: shorten at a word boundary, say so
        res = _hard_cut(res, max(limit - 4, 10))
    return res
