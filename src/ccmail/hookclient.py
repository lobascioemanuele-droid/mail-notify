"""Tiny hook client: forward the hook's stdin JSON to the local daemon. Never fails, never blocks Claude Code.

Kept import-light on purpose (runs on every prompt). Decisions live in the daemon.
"""
import http.client
import json
import os
import subprocess
import sys
import time

from . import paths

MAX_STDIN = 1_000_000
MAX_SPOOL_FILES = 200


def _target():
    try:
        port = json.loads(paths.info_path().read_text(encoding="utf-8"))["port"]
    except Exception:
        port = paths.DEFAULT_PORT
    try:
        token = paths.token_path().read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    return port, token


def _post(payload: bytes, want=None) -> bool:
    """POST the event. If `want` is a dict it receives the daemon's JSON answer."""
    port, token = _target()
    if not token:
        return False
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("POST", "/v1/events", body=payload,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        r = c.getresponse()
        ok = r.status == 200
        body = r.read()
        c.close()
        if ok and want is not None:
            try:
                want.update(json.loads(body or b"{}"))
            except ValueError:
                pass
        return ok
    except OSError:
        return False


def start_daemon() -> None:
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.name == "nt" and os.path.exists(w):
        exe = w
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                               | subprocess.CREATE_NO_WINDOW)
    else:
        kw["start_new_session"] = True  # survive the hook process and the terminal that started Claude Code
    subprocess.Popen([exe, "-m", "ccmail", "daemon"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **kw)


def _spool(payload: dict) -> None:
    d = paths.spool_dir()
    if len(list(d.glob("*.json"))) >= MAX_SPOOL_FILES:
        return
    name = f"{time.time_ns()}-{os.getpid()}.json"
    (d / name).write_text(json.dumps({"received_at": time.time(), "payload": payload}), encoding="utf-8")


def _emit_context(payload: dict, answer: dict) -> None:
    """If the daemon handed over a pending e-mail reply, give it to Claude Code as additionalContext (only the
    synchronous hooks PostToolBatch and UserPromptSubmit can do this; an async hook's output is ignored)."""
    text = answer.get("context")
    ev = payload.get("hook_event_name")
    if text and ev in ("PostToolBatch", "UserPromptSubmit"):
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": ev, "additionalContext": text}},
                                    ensure_ascii=False))
        sys.stdout.flush()


def main() -> int:
    try:
        if not paths.enabled_flag().exists():
            return 0  # switch is off: do nothing at all
        raw = sys.stdin.buffer.read(MAX_STDIN)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")  # stable project root (cwd may be a subfolder)
        if project_dir:
            payload["project_dir"] = project_dir
            raw = json.dumps(payload).encode("utf-8")
        answer = {}
        if _post(raw, answer):
            _emit_context(payload, answer)
            return 0
        start_daemon()
        for _ in range(8):  # wait up to ~2s for the daemon to come up
            time.sleep(0.25)
            if _post(raw, answer):
                _emit_context(payload, answer)
                return 0
        _spool(payload)
    except Exception:
        pass
    return 0
