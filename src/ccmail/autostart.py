"""Start the daemon at logon, per operating system, for the current user only (never elevated).

Windows  Task Scheduler (schtasks, ONLOGON, limited)
macOS    LaunchAgent in ~/Library/LaunchAgents
Linux    systemd user unit; falls back to an XDG autostart .desktop entry when systemd --user is not usable

The text generators are pure functions so they can be tested on any machine.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

TASK_NAME = "ccmail"
LABEL = "com.ccmail.daemon"
UNIT = "ccmail.service"


def daemon_argv() -> list:
    exe = sys.executable
    if os.name == "nt":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            exe = w
    return [exe, "-m", "ccmail", "daemon"]


def launchd_plist(argv: list) -> str:
    items = "\n".join(f"    <string>{_xml(a)}</string>" for a in argv)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            f"  <key>Label</key>\n  <string>{LABEL}</string>\n"
            f"  <key>ProgramArguments</key>\n  <array>\n{items}\n  </array>\n"
            "  <key>RunAtLoad</key>\n  <true/>\n"
            "  <key>KeepAlive</key>\n  <dict>\n    <key>SuccessfulExit</key>\n    <false/>\n  </dict>\n"
            "</dict>\n</plist>\n")


def systemd_unit(argv: list) -> str:
    cmd = " ".join(_sd_quote(a) for a in argv)
    return ("[Unit]\nDescription=ccmail - Claude Code e-mail notifications\n\n"
            f"[Service]\nExecStart={cmd}\nRestart=on-failure\nRestartSec=5\n\n"
            "[Install]\nWantedBy=default.target\n")


def desktop_entry(argv: list) -> str:
    cmd = " ".join(_sd_quote(a) for a in argv)
    return ("[Desktop Entry]\nType=Application\nName=ccmail\nComment=Claude Code e-mail notifications\n"
            f"Exec={cmd}\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n")


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _sd_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"' if (" " in s or '"' in s) else s


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def unit_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "systemd" / "user" / UNIT


def desktop_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "ccmail.desktop"


def _run(cmd) -> bool:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(cmd, capture_output=True, timeout=30, creationflags=flags).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _systemd_usable() -> bool:
    return bool(shutil.which("systemctl")) and _run(["systemctl", "--user", "show-environment"])


def enable() -> str:
    """Returns the mechanism used ('task-scheduler', 'launchd', 'systemd', 'xdg-autostart') or '' on failure."""
    argv = daemon_argv()
    if os.name == "nt":
        cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON", "/RL", "LIMITED", "/F",
               "/TR", f'"{argv[0]}" -m ccmail daemon']
        return "task-scheduler" if _run(cmd) else ""
    if sys.platform == "darwin":
        p = plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(launchd_plist(argv), encoding="utf-8")
        uid = str(os.getuid())
        _run(["launchctl", "bootout", f"gui/{uid}", str(p)])
        return "launchd" if _run(["launchctl", "bootstrap", f"gui/{uid}", str(p)]) else ""
    if _systemd_usable():
        u = unit_path()
        u.parent.mkdir(parents=True, exist_ok=True)
        u.write_text(systemd_unit(argv), encoding="utf-8")
        if _run(["systemctl", "--user", "daemon-reload"]) and _run(["systemctl", "--user", "enable", "--now", UNIT]):
            return "systemd"
    d = desktop_path()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(desktop_entry(argv), encoding="utf-8")
    return "xdg-autostart"


def disable() -> bool:
    ok = False
    if os.name == "nt":
        return _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if sys.platform == "darwin":
        p = plist_path()
        if p.exists():
            _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)])
            p.unlink()
            ok = True
        return ok
    if unit_path().exists():
        _run(["systemctl", "--user", "disable", "--now", UNIT])
        unit_path().unlink()
        _run(["systemctl", "--user", "daemon-reload"])
        ok = True
    if desktop_path().exists():
        desktop_path().unlink()
        ok = True
    return ok


def is_enabled() -> bool:
    if os.name == "nt":
        return _run(["schtasks", "/Query", "/TN", TASK_NAME])
    if sys.platform == "darwin":
        return plist_path().exists()
    return unit_path().exists() or desktop_path().exists()
