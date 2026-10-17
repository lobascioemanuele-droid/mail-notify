"""What Claude said last in a turn, read from the chat transcript, for the Stop events whose hook payload carries no
`last_assistant_message` (a turn that ends with a plan to approve, a question to answer, or tool calls only).

Only the tail of the file is read, only when the payload has no message, and nothing is written. The text goes through
the same content mode and redaction as any other message (see render/policy)."""
import json
from pathlib import Path

TAIL_BYTES = 1024 * 1024
MAX_CHARS = 6000


def _is_user_prompt(msg: dict) -> bool:
    """A real prompt from the user (not a tool result handed back to Claude)."""
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip() for b in content)
    return False


def _questions_text(inp: dict) -> str:
    out = []
    for q in inp.get("questions") or []:
        if not isinstance(q, dict):
            continue
        out.append(str(q.get("question") or "").strip())
        for opt in q.get("options") or []:
            if isinstance(opt, dict) and opt.get("label"):
                desc = f": {opt['description']}" if opt.get("description") else ""
                out.append(f"- {opt['label']}{desc}")
    return "\n".join(x for x in out if x)


def _block_text(block: dict) -> str:
    kind = block.get("type")
    if kind == "text":
        return str(block.get("text") or "").strip()
    if kind == "tool_use":
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        if block.get("name") == "ExitPlanMode":
            return str(inp.get("plan") or "").strip()  # the plan Claude asks you to approve
        if block.get("name") == "AskUserQuestion":
            return _questions_text(inp)
    return ""


def last_turn_text(path) -> str:
    """The last thing Claude wrote in the current turn: a plan to approve, the questions it asks, or its last text.
    Empty when the transcript is missing, unreadable or the turn has no such text."""
    if not path:
        return ""
    try:
        p = Path(path)
        size = p.stat().st_size
        with p.open("rb") as fh:
            fh.seek(max(0, size - TAIL_BYTES))
            tail = fh.read()
    except OSError:
        return ""
    for raw in reversed(tail.split(b"\n")):
        if not raw.strip():
            continue
        try:
            o = json.loads(raw)
        except ValueError:
            continue  # also the cut first line of the tail
        if not isinstance(o, dict) or o.get("isSidechain"):
            continue
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        if o.get("type") == "user":
            if _is_user_prompt(msg):
                return ""  # reached the prompt that started the turn: nothing from Claude in between
            continue
        if o.get("type") != "assistant":
            continue
        for block in reversed(msg.get("content") or []):
            if isinstance(block, dict):
                text = _block_text(block)
                if text:
                    return text[:MAX_CHARS]
    return ""
