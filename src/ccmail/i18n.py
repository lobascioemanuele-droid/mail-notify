"""Language of the mails and CLI texts: Italian ("it") or English ("en").

The setting `language` is "auto" (default: follow the system locale), "it" or "en". Texts are written inline at
the call site as an (it, en) pair, which keeps each sentence next to the logic that builds it.
"""
import locale
import os


def system_language() -> str:
    forced = os.environ.get("CCMAIL_LANG", "").lower()
    if forced in ("it", "en"):
        return forced
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
