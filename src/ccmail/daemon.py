"""Daemon entry: single instance (exclusive port bind), spool import, worker thread, HTTP server."""
import json
import logging
import logging.handlers
import os
import threading
import time

from . import auth, paths, policy
from .db import DB
from .server import Server
from .worker import Worker

log = logging.getLogger("ccmail")


def _setup_logging(foreground: bool):
    h = logging.handlers.RotatingFileHandler(paths.log_path(), maxBytes=512_000, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("ccmail")
    root.setLevel(logging.INFO)
    root.addHandler(h)
    if foreground:
        root.addHandler(logging.StreamHandler())


def import_spool(db):
    for f in sorted(paths.spool_dir().glob("*.json")):
        try:
            item = json.loads(f.read_text(encoding="utf-8"))
            policy.handle_event(db, item["payload"], received_at=item.get("received_at"))
        except Exception:
            log.warning("bad spool file skipped")
        finally:
            f.unlink(missing_ok=True)


def _ccmail_answers(port: int, token: str) -> bool:
    """True if a mail-notify daemon (one that accepts our token) is listening on the port."""
    import http.client
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        c.request("GET", "/v1/status", headers={"Authorization": f"Bearer {token}"})
        ok = c.getresponse().status == 200
        c.close()
        return ok
    except OSError:
        return False


def run(foreground=False, port=None) -> int:
    _setup_logging(foreground)
    db = DB(paths.db_path())
    db.purge(time.time())
    settings = db.get_settings()
    token = auth.load_or_create_token()
    if not paths.harden():
        log.warning("could not restrict ACL on the data dir; token and database are readable per default ACL")
    worker = Worker(db)
    holder = {}

    def shutdown():
        threading.Thread(target=holder["srv"].shutdown, daemon=True).start()

    want = settings["port"] if port is None else port
    try:
        srv = Server(("127.0.0.1", want), db, auth.Auth(token), worker, shutdown)
    except OSError:
        if _ccmail_answers(want, token):
            log.error("port %d: a mail-notify daemon is already running", want)
            return 1
        log.warning("port %d is taken by another program: picking a free port (clients read it from daemon.json)",
                    want)
        try:
            srv = Server(("127.0.0.1", 0), db, auth.Auth(token), worker, shutdown)
        except OSError:
            log.error("no free local port available")
            return 1
    holder["srv"] = srv
    paths.info_path().write_text(json.dumps({"pid": os.getpid(), "port": srv.server_address[1]}), encoding="utf-8")
    if settings["enabled"]:
        paths.enabled_flag().touch()
    try:  # bring stored sessions in line with the chat metadata and the project rules
        from . import chats, projroot
        chats.scan(db, force=True)
        log.info("startup repair: %s", projroot.repair(db))
    except Exception:
        log.exception("startup repair failed")
    import_spool(db)
    worker.start()
    from .inbound import Inbound
    inbound = Inbound(db)
    inbound.on_message = srv.heartbeat.message_arrived  # wakes the hook parked for that session
    inbound.is_parked = srv.heartbeat.is_parked          # lets the confirmation be honest about the chat
    inbound.on_enqueue = worker.wake.set                 # confirmation mails leave within seconds
    inbound.start()
    log.info("mail-notify daemon listening on 127.0.0.1:%d", srv.server_address[1])
    try:
        srv.serve_forever()
    finally:
        worker.stop_flag.set()
        worker.wake.set()
        inbound.stop_flag.set()
        paths.info_path().unlink(missing_ok=True)
        srv.server_close()
        db.close()
    return 0
