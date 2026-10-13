"""A short, recognisable command: `mail-notify open | status | test | stop | ...`.

`pip install` also creates `mail-notify` (and `ccmail`) as console scripts, but with the Microsoft Store Python the
Scripts folder is not on PATH, so the command is "not found" in a terminal. `install-cli` writes a tiny launcher
into a folder that is already on PATH (Windows: %LOCALAPPDATA%\\Microsoft\\WindowsApps; macOS/Linux: ~/.local/bin)
that runs `<this python> -m ccmail`. The launcher carries a marker line, so `uninstall-cli` removes only the file
this tool wrote and never someone else's.
"""
import os
import stat
import sys
from pathlib import Path

NAME = "mail-notify"
MARKER = "ccmail-launcher"


def _windows() -> bool:
    return os.name == "nt"


def target(system_windows: bool = None, env=None, home: Path = None) -> Path:
    env = os.environ if env is None else env
    win = _windows() if system_windows is None else system_windows
    if win:
        base = env.get("LOCALAPPDATA") or str(Path(home or Path.home()) / "AppData" / "Local")
        return Path(base) / "Microsoft" / "WindowsApps" / f"{NAME}.cmd"
    return Path(home or Path.home()) / ".local" / "bin" / NAME


def content(python: str, system_windows: bool = None) -> str:
    win = _windows() if system_windows is None else system_windows
    if win:
        return f'@echo off\r\nrem {MARKER}\r\nset "CCMAIL_PROG={NAME}"\r\n"{python}" -m ccmail %*\r\n'
    return f'#!/bin/sh\n# {MARKER}\nCCMAIL_PROG={NAME} exec "{python}" -m ccmail "$@"\n'


def is_ours(path: Path) -> bool:
    try:
        return MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def on_path(folder: Path, env=None) -> bool:
    env = os.environ if env is None else env
    parts = [p.strip().strip('"') for p in str(env.get("PATH", "")).split(os.pathsep) if p.strip()]
    norm = lambda s: os.path.normcase(os.path.normpath(s))  # noqa: E731
    return norm(str(folder)) in {norm(p) for p in parts}


def install(python: str = None, system_windows: bool = None, env=None, home: Path = None) -> tuple:
    """-> (path, on_path). Replaces an earlier launcher of ours; refuses to overwrite a foreign file."""
    win = _windows() if system_windows is None else system_windows
    path = target(win, env, home)
    if path.exists() and not is_ours(path):
        raise FileExistsError(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content(python or sys.executable, win)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(data)
    if not win:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path, on_path(path.parent, env)


def uninstall(system_windows: bool = None, env=None, home: Path = None):
    """-> the removed path, or None when there was nothing of ours to remove."""
    path = target(system_windows, env, home)
    if path.exists() and is_ours(path):
        path.unlink()
        return path
    return None
