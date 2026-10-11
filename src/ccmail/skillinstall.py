"""Install the packaged `mail-notify` skill into Claude Code's skills folder (run by the user, never by Claude)."""
import re
import time
from pathlib import Path

from . import paths

_VERSION = re.compile(r"ccmail-skill-version:\s*([0-9][^\s]*)")


def packaged_path() -> Path:
    return Path(__file__).with_name("skill") / "SKILL.md"


def target_path() -> Path:
    return paths.claude_dir() / "skills" / "mail-notify" / "SKILL.md"


def version_of(text: str):
    m = _VERSION.search(text or "")
    return m.group(1) if m else None


def state() -> dict:
    """{'installed': bool, 'ours': bool, 'version': str|None, 'current': bool, 'path': str}"""
    t = target_path()
    want = version_of(packaged_path().read_text(encoding="utf-8"))
    if not t.exists():
        return {"installed": False, "ours": False, "version": None, "current": False, "path": str(t)}
    have = version_of(t.read_text(encoding="utf-8", errors="replace"))
    return {"installed": True, "ours": have is not None, "version": have, "current": have == want, "path": str(t)}


def install() -> dict:
    """Copy the skill. A file that is not ccmail's (no version marker) is kept as a timestamped backup."""
    t = target_path()
    src = packaged_path().read_text(encoding="utf-8")
    backup = None
    if t.exists():
        old = t.read_bytes()
        if old != src.encode("utf-8") and version_of(old.decode("utf-8", "replace")) is None:
            backup = t.with_name(f"SKILL.md.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            backup.write_bytes(old)
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(src, encoding="utf-8")
    return {"path": str(t), "backup": str(backup) if backup else None}


def uninstall() -> bool:
    t = target_path()
    if t.exists() and version_of(t.read_text(encoding="utf-8", errors="replace")) is not None:
        t.unlink()
        try:
            t.parent.rmdir()
        except OSError:
            pass
        return True
    return False
