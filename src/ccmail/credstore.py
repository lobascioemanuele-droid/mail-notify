"""SMTP credential storage.

Primary: Windows Credential Manager (advapi32 CredWriteW/CredReadW via ctypes).
Fallback: DPAPI-protected file in the data dir (bound to the Windows user) with
restricted ACL. The fallback is announced loudly by callers (init/status/web app).
The password is never logged and never returned by any API.
"""
import base64
import json
import os

from . import paths

TARGET = "ccmail:smtp"
BACKEND_CREDMAN = "credential-manager"
BACKEND_FILE = "dpapi-file"
_ERROR_NOT_FOUND = 1168
_ENTROPY = b"ccmail-v1"


class CredError(Exception):
    pass


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt = ctypes.WinDLL("crypt32", use_last_error=True)

    _adv.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    _adv.CredWriteW.restype = wintypes.BOOL
    _adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    _adv.CredReadW.restype = wintypes.BOOL
    _adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _adv.CredDeleteW.restype = wintypes.BOOL
    _adv.CredFree.argtypes = [ctypes.c_void_p]
    _k32.LocalFree.argtypes = [ctypes.c_void_p]
    _crypt.CryptProtectData.argtypes = [ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(_BLOB)]
    _crypt.CryptProtectData.restype = wintypes.BOOL
    _crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(_BLOB)]
    _crypt.CryptUnprotectData.restype = wintypes.BOOL

    def _blob(data: bytes):
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf

    def _credman_write(target, user, password):
        data = password.encode("utf-16-le")
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        c = _CREDENTIAL()
        c.Type = 1  # CRED_TYPE_GENERIC
        c.TargetName = target
        c.UserName = user
        c.CredentialBlobSize = len(data)
        c.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        c.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
        if not _adv.CredWriteW(ctypes.byref(c), 0):
            raise CredError(f"CredWrite failed (error {ctypes.get_last_error()})")

    def _credman_read(target):
        ptr = ctypes.POINTER(_CREDENTIAL)()
        if not _adv.CredReadW(target, 1, 0, ctypes.byref(ptr)):
            err = ctypes.get_last_error()
            if err == _ERROR_NOT_FOUND:
                return None
            raise CredError(f"CredRead failed (error {err})")
        try:
            c = ptr.contents
            raw = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
            return c.UserName or "", raw.decode("utf-16-le")
        finally:
            _adv.CredFree(ptr)

    def _credman_delete(target):
        _adv.CredDeleteW(target, 1, 0)

    def _dpapi(data: bytes, protect: bool) -> bytes:
        inb, _k1 = _blob(data)
        ent, _k2 = _blob(_ENTROPY)
        out = _BLOB()
        fn = _crypt.CryptProtectData if protect else _crypt.CryptUnprotectData
        if not fn(ctypes.byref(inb), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out)):
            raise CredError(f"DPAPI failed (error {ctypes.get_last_error()})")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _k32.LocalFree(out.pbData)
else:  # pragma: no cover - Windows is the only tested target
    def _credman_write(target, user, password):
        raise CredError("Credential Manager is only available on Windows")

    def _credman_read(target):
        return None

    def _credman_delete(target):
        pass

    def _dpapi(data, protect):
        raise CredError("DPAPI is only available on Windows")


def default_target() -> str:
    """Global entry for the real install; a distinct one per CCMAIL_HOME so test/dev data dirs
    can never read (or overwrite) the user's real credentials."""
    home = os.environ.get("CCMAIL_HOME")
    if not home:
        return TARGET
    import hashlib
    return f"{TARGET}:{hashlib.sha1(os.path.abspath(home).lower().encode()).hexdigest()[:10]}"


def _file_path():
    return paths.data_dir() / "smtp.cred"


def save(user: str, password: str, target: str = None) -> str:
    """Store credentials; returns the backend used."""
    target = target or default_target()
    try:
        _credman_write(target, user, password)
        if _file_path().exists():
            _file_path().unlink()
        return BACKEND_CREDMAN
    except CredError:
        pass
    blob = _dpapi(password.encode("utf-8"), True)
    f = _file_path()
    f.write_text(json.dumps({"user": user, "blob": base64.b64encode(blob).decode()}), encoding="utf-8")
    paths.restrict_acl(f)
    return BACKEND_FILE


def load(target: str = None):
    """Return (user, password, backend) or None."""
    target = target or default_target()
    try:
        r = _credman_read(target)
        if r:
            return r[0], r[1], BACKEND_CREDMAN
    except CredError:
        pass
    f = _file_path()
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        pw = _dpapi(base64.b64decode(d["blob"]), False).decode("utf-8")
        return d["user"], pw, BACKEND_FILE
    return None


def describe(target: str = None):
    """Non-secret summary: (user, backend) or None. Does not expose the password."""
    r = load(target)
    return (r[0], r[2]) if r else None


def delete(target: str = None):
    target = target or default_target()
    _credman_delete(target)
    f = _file_path()
    if f.exists():
        f.unlink()

