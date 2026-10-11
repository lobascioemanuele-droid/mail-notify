"""Local auth: a file token for hooks/CLI (Bearer), and cookie sessions + CSRF for the web app."""
import hmac
import secrets
import threading
import time

from . import paths

SESSION_TTL = 8 * 3600
NONCE_TTL = 60


def load_or_create_token() -> str:
    p = paths.token_path()
    if p.exists():
        t = p.read_text(encoding="utf-8").strip()
        if len(t) >= 32:
            return t
    t = secrets.token_urlsafe(32)
    p.write_text(t, encoding="utf-8")
    paths.restrict_acl(p)
    return t


def read_token():
    try:
        return paths.token_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


class Auth:
    def __init__(self, token: str):
        self.token = token
        self._lock = threading.Lock()
        self._nonces = {}
        self._sessions = {}

    def check_bearer(self, header) -> bool:
        if not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].strip().encode(), self.token.encode())

    def new_nonce(self) -> str:
        n = secrets.token_urlsafe(24)
        with self._lock:
            self._nonces = {k: v for k, v in self._nonces.items() if v > time.time()}
            self._nonces[n] = time.time() + NONCE_TTL
        return n

    def redeem_nonce(self, n: str):
        """Single use. Returns (session_id, csrf) or None."""
        with self._lock:
            exp = self._nonces.pop(n, 0)
            if exp < time.time():
                return None
            sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self._sessions[sid] = (time.time() + SESSION_TTL, csrf)
            return sid, csrf

    def session_csrf(self, sid):
        with self._lock:
            exp, csrf = self._sessions.get(sid or "", (0, None))
            if exp < time.time():
                self._sessions.pop(sid or "", None)
                return None
            return csrf
