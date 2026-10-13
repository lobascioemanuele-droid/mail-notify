"""Language of the mails and CLI texts: Italian ("it") or English ("en").

The setting `language` is "auto" (default: follow Claude Code's own `language` setting when it names Italian or
English, else the system locale), "it" or "en". Texts are written inline at
the call site as an (it, en) pair, which keeps each sentence next to the logic that builds it.
"""
import locale
import os
import time


_claude_cache = {"key": None, "value": None}


def claude_language():
    """'it' / 'en' when Claude Code's settings.json has a `language` that names one of them, else None.
    Only that one key is read (never anything else from the file); the result is cached per file timestamp."""
    try:
        from . import paths
        path = paths.claude_dir() / "settings.json"
        now = time.monotonic()
        c = _claude_cache
        if c["key"] is not None and c.get("path") == str(path) and now - c.get("at", 0.0) < 2.0:
            return c["value"]  # pick() is called many times per mail: look at the file at most every 2 s
        try:
            st = path.stat()
        except OSError:
            c.update(key="missing", value=None, path=str(path), at=now)
            return None
        key = (str(path), st.st_mtime_ns)
        if c["key"] == key:
            c.update(path=str(path), at=now)
            return c["value"]
        import json
        value = None
        lang = str((json.loads(path.read_text(encoding="utf-8")) or {}).get("language") or "").strip().lower()
        if lang.startswith(("it", "ital")):
            value = "it"
        elif lang.startswith(("en", "engl")):
            value = "en"
        _claude_cache.update(key=key, value=value, path=str(path), at=now)
        return value
    except Exception:
        return None


def system_language() -> str:
    forced = os.environ.get("CCMAIL_LANG", "").lower()
    if forced in ("it", "en"):
        return forced
    claude = claude_language()
    if claude:
        return claude
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "")
        if v:
            return "it" if v.lower().startswith("it") else "en"
    try:
        loc = locale.getlocale()[0] or ""
    except (ValueError, TypeError):
        loc = ""
    if not loc and os.name == "nt":
        try:
            import ctypes
            lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            return "it" if lid == 0x10 else "en"
        except Exception:
            return "en"
    return "it" if loc.lower().startswith("it") else "en"


def resolve(settings=None) -> str:
    v = (settings or {}).get("language", "auto")
    if v in ("it", "en"):
        return v
    return system_language()


def pick(settings, it: str, en: str) -> str:
    return en if resolve(settings) == "en" else it
