"""`ccmail wake`: async+asyncRewake Stop hook. Exits 2 (with the message on stderr) only when the daemon
decides Claude must be woken (an e-mail reply arrived, or background tasks need a status check); otherwise
exits 0 silently. Never raises.

The hook parks inside the daemon for a long time. If the daemon restarts meanwhile the connection drops: the
client reconnects (for a limited time) so the chat does not silently stop listening."""
import http.client
import json
import sys
import time

from . import paths
from .hookclient import _target

RECONNECT_FOR_S = 15 * 60   # how long to keep trying after the daemon went away
RECONNECT_EVERY_S = 3


def _ask(raw: bytes):
    port, token = _target()
    if not token:
        return None
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=None)  # parked until the daemon answers
    try:
        c.request("POST", "/v1/heartbeat/wait", body=raw,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    finally:
        c.close()


def main() -> int:
    try:
        if not paths.enabled_flag().exists():
            return 0
        raw = sys.stdin.buffer.read(1_000_000)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        # The daemon decides whether this session is worth parking for (running tasks, or a reply may
        # arrive); when it is not, it answers at once and this process exits.
        give_up = None
        while True:
            try:
                res = _ask(raw)
            except (OSError, http.client.HTTPException):
                now = time.time()
                give_up = give_up or now + RECONNECT_FOR_S
                if now >= give_up:
                    return 0
                time.sleep(RECONNECT_EVERY_S)  # daemon restarting: try again and re-park
                continue
            if res is None:
                return 0
            status, data = res
            if status == 200 and data.get("wake") and data.get("message"):
                sys.stderr.write(str(data["message"]))
                return 2
            return 0
    except Exception:
        pass
    return 0
