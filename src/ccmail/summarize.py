"""Extractive summary of an assistant message (no model, no network). Deterministic and local.

The summary covers the WHOLE message in at most MAX_LINES short lines:
  * a lead (the first paragraph, up to two sentences),
  * one line per section: "Heading: first sentence" (or its first bullets) so that later sections are represented
    too, not only the beginning,
  * a closing question, when the message ends by asking something.
When there are more lines than room, the sections are sampled evenly across the message and the rest is counted
("(+N altri punti)"). Never cut a sentence in the middle: a line is only shortened at a word boundary and marked
with "[…]" when it exceeds the per-line cap. Code blocks and tables are left out.
"""
import re

_FENCE = re.compile(r"```.*?(```|$)", re.S)
_MD = re.compile(r"(\*\*|__|`|^#{1,6}\s*|^>\s*)", re.M)
_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)")
_HEAD = re.compile(r"^\s*#{1,6}\s+(.*\S)")
_RULE = re.compile(r"^\s*(?:[-*_=]\s*){3,}$")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ0-9\"'(\[])")
MAX_LINES = 10    # entries in the summary, "(+N altri punti)" and the question included
LINE_CAP = 130    # a single line longer than this is cut at a word boundary
ITEM_CAP = 100    # the bullet a section line is built from
LEAD_SENTENCES = 2
LEAD_CAP = 220


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _MD.sub("", s)).strip(" -–:")


def _sentences(s: str) -> list:
    return [p.strip() for p in _SENT.split(s) if p.strip()]


def _hard_cut(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + " […]"


def _first_sentence(s: str) -> str:
    sents = _sentences(s)
    return sents[0] if sents else s


def _blocks(text: str) -> list:
    """[(kind, text)] in document order; kind is head | para | item. Tables and rules are dropped."""
    out, para = [], []

    def flush():
        if para:
            out.append(("para", _clean(" ".join(para))))
            para.clear()

    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("|"):
            flush()
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cells and not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                if out and out[-1][0] == "table":
                    out[-1] = ("table", out[-1][1] + [cells[0]])
                else:
                    out.append(("table", [cells[0]]))  # first row is the header
            continue
        if not s or _RULE.match(raw):
            flush()
            continue
        m = _HEAD.match(raw)
        if m:
            flush()
            out.append(("head", _clean(m.group(1))))
            continue
        m = _ITEM.match(raw)
        if m:
            flush()
            out.append(("item", _clean(m.group(1))))
            continue
        if raw[:1] in (" ", "\t") and out and out[-1][0] == "item" and not para:  # wrapped bullet
            out[-1] = ("item", out[-1][1] + " " + _clean(s))
            continue
        para.append(s)
    flush()
    return [(k, v) for k, v in out if v and (k != "table" or len(v) > 1)]


def _sample(items: list, k: int) -> list:
    """k entries spread evenly over the list, order kept."""
    if k >= len(items):
        return list(items)
    if k <= 0:
        return []
    if k == 1:
        return [items[0]]
    idx = sorted({round(i * (len(items) - 1) / (k - 1)) for i in range(k)})
    return [items[i] for i in idx]


def summarize(text: str, limit: int = 600) -> str:
    text = _FENCE.sub("", text or "").strip()
    if not text:
        return ""
    blocks = _blocks(text)
    if not blocks:
        return _hard_cut(_clean(text), min(limit, LEAD_CAP))

    # sections: a heading opens one; its content keeps document order (prose and bullets interleaved)
    sections = [{"head": None, "seq": []}]
    for kind, val in blocks:
        if kind == "head":
            sections.append({"head": val, "seq": []})
        else:
            sections[-1]["seq"].append((kind, val))

    lead = ""
    for sec in sections:  # the first paragraph of the message, wherever it sits
        i = next((n for n, (k, _v) in enumerate(sec["seq"]) if k == "para"), None)
        if i is not None:
            sents = _sentences(sec["seq"].pop(i)[1])
            lead = " ".join(sents[:LEAD_SENTENCES])
            if len(lead) > limit:
                lead = sents[0]
            lead = _hard_cut(lead, min(LEAD_CAP, limit))
            break

    question = ""
    kind, val = blocks[-1]
    if kind in ("para", "item") and isinstance(val, str) and val.endswith("?"):
        question = _hard_cut(val, LEAD_CAP)
        for sec in sections:  # do not repeat it as a body line
            sec["seq"] = [x for x in sec["seq"] if x[1] != val]

    entries = []
    for sec in sections:
        seq = sec["seq"]
        if sec["head"]:
            body = ""
            if seq and seq[0][0] == "para":
                body = _first_sentence(seq[0][1])  # what the section opens with; a closing paragraph is not it
            elif seq and seq[0][0] == "table":
                body = ", ".join(_clean(c) for c in seq[0][1][1:4] if _clean(c))
            elif seq and seq[0][0] == "item":
                body = _hard_cut(_first_sentence(seq[0][1]), ITEM_CAP)
            if body:
                entries.append(_hard_cut(f"{sec['head']}: {body.rstrip(':')}", LINE_CAP))
        else:
            for k, v in seq:
                if k == "table":
                    continue
                entries.append(_hard_cut(_first_sentence(v), LINE_CAP) if k == "para"
                               else "• " + _hard_cut(_first_sentence(v), LINE_CAP))

    lead_lines = [lead] if lead else []
    tail_lines = [question] if question else []

    def assemble(chosen: list, hidden: int) -> str:
        more = [f"(+{hidden} altri punti)"] if hidden > 0 else []
        return "\n".join(lead_lines + chosen + more + tail_lines)

    room = MAX_LINES - len(lead_lines) - len(tail_lines)
    if len(entries) > room:
        chosen = _sample(entries, max(room - 1, 0))  # one line is the "(+N altri punti)" counter
    else:
        chosen = list(entries)
    res = assemble(chosen, len(entries) - len(chosen))
    while len(res) > limit and chosen:  # character budget: drop from the middle, keep the ends
        chosen.pop(len(chosen) // 2)
        res = assemble(chosen, len(entries) - len(chosen))
    if not res:
        res = _hard_cut(_clean(text), min(limit, LEAD_CAP))
    if len(res) > limit:  # a cap smaller than one sentence: shorten at a word boundary, say so
        res = _hard_cut(res, max(limit - 4, 10))
    return res
