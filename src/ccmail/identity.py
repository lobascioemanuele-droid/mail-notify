"""The Claude account's display name ("Alice"), read from Claude Code's own config.

Only `oauthAccount.displayName` is read from ~/.claude.json; nothing else in that file is used or stored.
"""
import json
from pathlib import Path

def _default_path() -> Path:
    import os
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(d) / ".claude.json" if d else Path.home() / ".claude.json"


_cache = {"mtime": None, "name": ""}


def claude_display_name(config_path=None) -> str:
    p = Path(config_path) if config_path else _default_path()
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return ""
    if _cache["mtime"] == mt and not config_path:
        return _cache["name"]
    name = ""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        acct = d.get("oauthAccount") if isinstance(d, dict) else None
        if isinstance(acct, dict):
            name = " ".join(str(acct.get("displayName") or "").split())[:60]
    except (OSError, ValueError):
        name = ""
    if not config_path:
        _cache.update(mtime=mt, name=name)
    return name
