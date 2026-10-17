"""Idempotent merge of ccmail hooks into Claude Code settings.json (with timestamped backup)."""
import json
import os
import sys
import time
from pathlib import Path

from . import paths

EVENTS = ["UserPromptSubmit", "Stop", "Notification", "StopFailure", "SessionStart", "SessionEnd"]
OURS = "-m ccmail hook"
WAKE = "-m ccmail wake"
WAKE_TIMEOUT_S = 15000  # must exceed the longest heartbeat_minutes (240 min) plus margin
LEGACY = "email_notify.py"
TASK_NAME = "ccmail"


def default_settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_SETTINGS") or paths.claude_dir() / "settings.json")


def hook_command() -> str:
    return f'"{sys.executable}" {OURS}'


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text) if text.strip() else {}


def _save(path: Path, data: dict):
    """Write the settings file; returns the backup path, or None when there was no file to back up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    bak = None
    if path.exists():
        bak = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        bak.write_bytes(path.read_bytes())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return bak


def _strip(data: dict, needle: str) -> int:
    """Remove hook handlers whose command contains `needle`; prune empty groups/events."""
    removed = 0
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        for g in groups:
            hs = g.get("hooks") if isinstance(g, dict) else None
            if isinstance(hs, list):
                keep = [h for h in hs if needle not in str(h.get("command", ""))]
                removed += len(hs) - len(keep)
                g["hooks"] = keep
        hooks[event] = [g for g in groups if not (isinstance(g, dict) and g.get("hooks") == [])]
        if not hooks[event]:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    return removed


def install(path: Path = None, events=EVENTS):
    """Merge our hooks into the settings file. Returns the backup path (None if the file did not exist)."""
    path = path or default_settings_path()
    data = _load(path)
    _strip(data, OURS)  # replace our own entries (e.g. interpreter path changed)
    _strip(data, WAKE)
    hooks = data.setdefault("hooks", {})
    for ev in events:
        hooks.setdefault(ev, []).append(
            {"matcher": "", "hooks": [{"type": "command", "command": hook_command(), "timeout": 10}]})
    # background-task awareness: Bash completions (async, never slows Claude) ...
    hooks.setdefault("PostToolUse", []).append({"matcher": "Bash", "hooks": [{
        "type": "command", "command": hook_command(), "timeout": 10, "async": True}]})
    # liveness: one async ping per round of tool calls, so the Attività view knows Claude is working
    # NOT async: a synchronous PostToolBatch hook may return additionalContext, which is how an e-mail reply
    # reaches Claude in the middle of a turn. The call is a few milliseconds to the local daemon.
    hooks.setdefault("PostToolBatch", []).append({"matcher": "", "hooks": [{
        "type": "command", "command": hook_command(), "timeout": 5}]})
    # ... and the periodic wake-up (parks in the daemon; exit 2 wakes Claude with a reminder)
    hooks.setdefault("Stop", []).append({"matcher": "", "hooks": [{
        "type": "command", "command": f'"{sys.executable}" {WAKE}', "async": True, "asyncRewake": True,
        "timeout": WAKE_TIMEOUT_S}]})
    return _save(path, data)


TRACE = "-m ccmail trace-hook"
TRACE_EVENTS = ["UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
                "Notification", "Stop", "StopFailure", "SubagentStart", "SubagentStop", "TaskCreated",
                "TaskCompleted", "SessionStart", "SessionEnd"]


def trace_install(path: Path = None) -> None:
    """Diagnostic hooks (async, so they never slow Claude down). Remove with trace_uninstall."""
    path = path or default_settings_path()
    data = _load(path)
    _strip(data, TRACE)
    hooks = data.setdefault("hooks", {})
    for ev in TRACE_EVENTS:
        hooks.setdefault(ev, []).append({"matcher": "", "hooks": [{
            "type": "command", "command": f'"{sys.executable}" {TRACE}', "async": True, "timeout": 10}]})
    _save(path, data)


def trace_uninstall(path: Path = None) -> int:
    path = path or default_settings_path()
    if not path.exists():
        return 0
    data = _load(path)
    n = _strip(data, TRACE)
    if n:
        _save(path, data)
    return n


def uninstall(path: Path = None, legacy: bool = False) -> int:
    path = path or default_settings_path()
    if not path.exists():
        return 0
    data = _load(path)
    n = _strip(data, OURS) + _strip(data, WAKE)
    if legacy:
        n += _strip(data, LEGACY)
    if n:
        _save(path, data)
    return n


def hook_interpreters(path: Path = None) -> list:
    """Executables referenced by our hook commands in settings.json (to spot a moved/removed Python)."""
    path = path or default_settings_path()
    found = set()
    try:
        data = _load(path)
    except (OSError, ValueError):
        return []
    for groups in (data.get("hooks") or {}).values():
        for g in groups if isinstance(groups, list) else []:
            for h in g.get("hooks", []) if isinstance(g, dict) else []:
                cmd = str(h.get("command", ""))
                if OURS in cmd or WAKE in cmd:
                    exe = cmd.split(" -m ccmail", 1)[0].strip().strip('"')
                    if exe:
                        found.add(exe)
    return sorted(found)


def has_legacy(path: Path = None) -> bool:
    path = path or default_settings_path()
    return LEGACY in json.dumps(_load(path))


def is_installed(path: Path = None) -> bool:
    path = path or default_settings_path()
    hooks = _load(path).get("hooks", {})
    batch = [h for g in hooks.get("PostToolBatch", []) if isinstance(g, dict) for h in g.get("hooks", [])
             if OURS in str(h.get("command", ""))]
    batch_sync = bool(batch) and not any(h.get("async") for h in batch)  # needed for in-turn delivery
    return (all(OURS in json.dumps(hooks.get(ev, [])) for ev in ("Stop", "Notification", "PostToolUse",
                                                                  "PostToolBatch"))
            and WAKE in json.dumps(hooks.get("Stop", [])) and batch_sync)


# ---- autostart: see autostart.py (Task Scheduler / launchd / systemd --user) ----
def autostart(on: bool) -> bool:
    from . import autostart as _a
    return bool(_a.enable()) if on else _a.disable()
