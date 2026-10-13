"""Diagnostic event recorder (`mail-notify trace on`): logs the shape of every hook payload to trace.jsonl.

Purpose: find out empirically which events Claude Code emits for background commands. Strings are
redacted and truncated; the file stays local. Independent from the notification pipeline.
"""
import json
import re
import sys
import time

from . import paths
from .redact import redact

MAX_BYTES = 5_000_000
STR_LIMIT = 300
INTERESTING = re.compile(r"background|task|shell|pid|bash_id|id$", re.I)


def _shrink(v, depth=0):
    if isinstance(v, str):
        v = redact(v)
        return v if len(v) <= STR_LIMIT else v[:STR_LIMIT] + f"…[+{len(v) - STR_LIMIT}]"
    if isinstance(v, dict):
        return {k: _shrink(x, depth + 1) for k, x in list(v.items())[:40]}
    if isinstance(v, list):
        return [_shrink(x, depth + 1) for x in v[:20]]
    return v


def trace_file():
    return paths.data_dir() / "trace.jsonl"


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read(1_000_000))
        f = trace_file()
        if f.exists() and f.stat().st_size > MAX_BYTES:
            return 0
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": round(time.time(), 2), **_shrink(payload)}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return 0


def summarize(limit=60):
    """Human-readable view of the last records: what happened and which background-related fields exist."""
    f = trace_file()
    if not f.exists():
        return ["(nessuna traccia: esegui 'mail-notify trace on', riavvia Claude Code e usa un comando in background)"]
    out = []
    for line in f.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = time.strftime("%H:%M:%S", time.localtime(r.get("ts", 0)))
        parts = [t, "s=" + str(r.get("session_id", "?"))[:6], r.get("hook_event_name", "?")]
        if r.get("tool_name"):
            parts.append(r["tool_name"])
        ti = r.get("tool_input") or {}
        if isinstance(ti, dict) and "run_in_background" in ti:
            parts.append(f"run_in_background={ti['run_in_background']}")
        tr = r.get("tool_response")
        if isinstance(tr, dict):
            hits = {k: v for k, v in tr.items() if INTERESTING.search(k)}
            parts.append("response_keys=" + ",".join(sorted(tr)))
            if hits:
                parts.append(f"bg-fields={json.dumps(hits, ensure_ascii=False)[:120]}")
        if r.get("notification_type"):
            parts.append(f"notification_type={r['notification_type']}")
        bt = r.get("background_tasks")
        if r.get("hook_event_name") in ("Stop", "SubagentStop") and bt == []:
            parts.append("background_tasks=[] (nessuno in esecuzione)")
        elif bt:
            parts.append("background_tasks=" + json.dumps(
                [{k: t_.get(k) for k in ("id", "type", "status")} for t_ in bt], ensure_ascii=False)[:160])
        if r.get("agent_type"):
            parts.append(f"agent={r['agent_type']}")
        out.append("  ".join(str(p) for p in parts))
    return out
