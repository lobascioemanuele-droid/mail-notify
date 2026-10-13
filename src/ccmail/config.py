"""Settings defaults and validation (the only gate for values coming from the web app)."""
import re

from .paths import DEFAULT_PORT

NOTIFICATION_TYPES = [
    "permission_prompt", "idle_prompt", "agent_needs_input", "agent_completed",
    "elicitation_dialog", "elicitation_url_dialog", "auth_success",
    "quota_auto_resume_fired", "quota_auto_resume_stale", "quota_auto_resume_disabled",
]
EVENT_KEYS = ["Stop", "Notification", "StopFailure", "UsageLimit", "SessionEnd"]

DEFAULTS = {
    "enabled": False,
    "port": DEFAULT_PORT,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_security": "ssl",          # ssl | starttls
    "recipient": "",
    "language": "auto",              # auto (system locale) | it | en: mails and CLI texts
    "device_name": "",               # optional, shown as "Dispositivo:" when you use ccmail on several PCs
    "account_name": "",              # shown in every mail as "Account:"; empty = Windows user name
    "content_mode": "summary",       # summary | full | title | minimal
    "max_body_chars": 1500,          # full mode: truncation of the whole message
    "summary_chars": 300,            # summary mode: cap of the extractive summary (characters)
    # --- fewer emails: every gate below suppresses mail ---
    "min_elapsed_s": 120,            # Stop mails only for turns at least this long
    "cooldown_s": 60,                # min seconds between mails for the same session
    "max_per_hour": 10,              # hard cap over all sessions
    "mute_after_toggle_s": 60,       # silence right after on/off
    "notify_types": ["permission_prompt", "agent_needs_input"],
    "events_enabled": {"Stop": True, "Notification": True, "StopFailure": True, "UsageLimit": True,
                       "SessionEnd": False},
    "suggestions_enabled": True,     # "comandi suggeriti" section in the mail body
    "inbound_enabled": False,        # reply-by-email control channel (IMAP); off until the user turns it on
    "html_mail": False,              # add an HTML alternative: quick-reply buttons and a scrollable full message
    "inbound_ack": True,             # instant confirmation mail for an accepted reply
    "inbound_to_claude": False,      # ALSO deliver free-text replies into the Claude chat (needs inbound_enabled)
    "imap_host": "imap.gmail.com",
    "inbound_poll_s": 60,
    "watchdog_minutes": 0,           # 0 = off
    # --- background processes: periodic status check ---
    "heartbeat_mode": "hook",        # hook: daemon wakes Claude via asyncRewake | skill: Claude self-schedules | off
    "heartbeat_minutes": 30,         # wake-up period while background tasks are running
    "notify_stop_with_bg": False,    # mail on every Stop even if background tasks are still running
    "toggled_at": 0,
    "snooze_until": 0,               # set only via POST /v1/snooze
}

_EMAIL = re.compile(r"^[^@\s,;<>\r\n]+@[^@\s,;<>\r\n]+$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


def _int(lo, hi):
    def f(v):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(f"must be an integer between {lo} and {hi}")
        return v
    return f


def _choice(*opts):
    def f(v):
        if v not in opts:
            raise ValueError(f"must be one of {opts}")
        return v
    return f


def _bool(v):
    if not isinstance(v, bool):
        raise ValueError("must be true or false")
    return v


def _name(v):
    if not isinstance(v, str) or len(v.strip()) > 60 or any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise ValueError("up to 60 characters, no control characters")
    return v.strip()


def _host(v):
    if not isinstance(v, str) or not _HOST.match(v):
        raise ValueError("invalid host")
    return v


def _recipient(v):
    if v == "":
        return v
    if not isinstance(v, str) or not _EMAIL.match(v) or len(v) > 254:
        raise ValueError("invalid email address")
    return v


def _types(v):
    if not isinstance(v, list) or any(t not in NOTIFICATION_TYPES for t in v):
        raise ValueError("unknown notification type")
    return sorted(set(v))


def _events(v):
    if not isinstance(v, dict) or set(v) - set(EVENT_KEYS) or any(not isinstance(x, bool) for x in v.values()):
        raise ValueError("invalid events map")
    return v


# Keys editable from the web app (enabled/port/toggled_at are excluded on purpose).
VALIDATORS = {
    "smtp_host": _host,
    "smtp_port": _int(1, 65535),
    "smtp_security": _choice("ssl", "starttls"),
    "recipient": _recipient,
    "account_name": _name,
    "device_name": _name,
    "language": _choice("auto", "it", "en"),
    "content_mode": _choice("summary", "full", "title", "minimal"),
    "max_body_chars": _int(200, 5000),
    "summary_chars": _int(100, 3000),
    "min_elapsed_s": _int(0, 86400),
    "cooldown_s": _int(0, 3600),
    "max_per_hour": _int(1, 200),
    "mute_after_toggle_s": _int(0, 600),
    "notify_types": _types,
    "events_enabled": _events,
    "watchdog_minutes": _int(0, 1440),
    "suggestions_enabled": _bool,
    "inbound_enabled": _bool,
    "inbound_to_claude": _bool,
    "html_mail": _bool,
    "inbound_ack": _bool,
    "imap_host": _host,
    "inbound_poll_s": _int(30, 600),
    "heartbeat_mode": _choice("hook", "skill", "off"),
    "heartbeat_minutes": _int(1, 240),
    "notify_stop_with_bg": _bool,
}


def check_combo(s: dict):
    """Cross-field rule: the silent-session watchdog must outlast at least one missed heartbeat.
    Returns an error string "key: reason" or None."""
    w, hb = s.get("watchdog_minutes", 0), s.get("heartbeat_minutes", 30)
    if w and s.get("heartbeat_mode") != "off" and w < 2 * hb:
        return (f"watchdog_minutes: con il controllo periodico ogni {hb} min deve essere 0 (spento) "
                f"oppure almeno {2 * hb} min")
    return None


def validate(patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise ValueError("body must be an object")
    out = {}
    for k, v in patch.items():
        if k not in VALIDATORS:
            raise ValueError(f"unknown or read-only setting: {k}")
        try:
            out[k] = VALIDATORS[k](v)
        except ValueError as e:
            raise ValueError(f"{k}: {e}") from None
    return out
