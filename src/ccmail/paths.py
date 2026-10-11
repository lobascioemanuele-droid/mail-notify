"""Filesystem locations. CCMAIL_HOME overrides the data dir (used by tests)."""
import os
import subprocess
from pathlib import Path

DEFAULT_PORT = 47821


def data_dir() -> Path:
    override = os.environ.get("CCMAIL_HOME")
    if override:
        p = Path(override)
    elif os.name == "nt":
        p = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "ccmail"
    else:
        p = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ccmail"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "ccmail.db"


def token_path() -> Path:
    return data_dir() / "hook.token"


def info_path() -> Path:
    return data_dir() / "daemon.json"


def enabled_flag() -> Path:
    return data_dir() / "enabled"


def log_path() -> Path:
    return data_dir() / "ccmail.log"


def spool_dir() -> Path:
    p = data_dir() / "spool"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _real_paths(path: Path):
    """Microsoft Store Python redirects writes under %APPDATA% into the package's LocalCache, where
    child processes such as icacls cannot see the original path. Yield the path itself, then the
    redirected equivalents that actually exist."""
    yield path
    appdata, local = os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")
    if not (appdata and local):
        return
    for root, sub in ((appdata, "Roaming"), (local, "Local")):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        for pkg in Path(local, "Packages").glob("PythonSoftwareFoundation.Python.*"):
            cand = pkg / "LocalCache" / sub / rel
            if cand.exists():
                yield cand


def restrict_acl(path: Path) -> bool:
    """Limit a file/dir to the current user (Windows icacls). Returns True only if icacls succeeded."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
            return True
        except OSError:
            return False
    user = os.environ.get("USERNAME")
    if not user:
        return False
    domain = os.environ.get("USERDOMAIN")
    who = f"{domain}\\{user}" if domain else user
    perm = "(OI)(CI)F" if path.is_dir() else "F"
    for target in _real_paths(path):
        try:
            r = subprocess.run(
                ["icacls", str(target), "/inheritance:r", "/grant:r", f"{who}:{perm}"],
                capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r.returncode == 0:
                return True
        except Exception:
            pass
    return False


def harden() -> bool:
    """Restrict the whole data dir to the current user; files inside inherit the ACL."""
    return restrict_acl(data_dir())
