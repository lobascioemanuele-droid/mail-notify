"""ccmail command line. `on`, `off`, `init` never send email; only `test` does, on explicit request."""
import argparse
import getpass
import http.client
import json
import sys
import time
import webbrowser

from . import __version__, auth, config, credstore, hookclient, installer, mailer, paths
from .db import DB, set_enabled


def _api(method, path, body=None, timeout=5):
    """Call the running daemon with the file token. Returns (status, json) or None if unreachable."""
    token = auth.read_token()
    try:
        port = json.loads(paths.info_path().read_text(encoding="utf-8"))["port"]
    except Exception:
        port = paths.DEFAULT_PORT
    if not token:
        return None
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        c.close()
        return r.status, data
    except OSError:
        return None


def _ensure_daemon() -> bool:
    auth.load_or_create_token()
    if _api("GET", "/v1/status"):
        return True
    hookclient.start_daemon()
    for _ in range(20):
        time.sleep(0.25)
        if _api("GET", "/v1/status"):
            return True
    return False


def cmd_init(a):
    db = DB(paths.db_path())
    cur = db.get_settings()
    host = input(f"SMTP host [{cur['smtp_host']}]: ").strip() or cur["smtp_host"]
    port = input(f"SMTP port [{cur['smtp_port']}]: ").strip() or cur["smtp_port"]
    user = input("Indirizzo Gmail mittente: ").strip()
    if a.visible:
        pw = input("App Password (VISIBILE mentre scrivi): ").replace(" ", "")
    else:
        pw = getpass.getpass("App Password (16 caratteri, nessun carattere viene mostrato): ").replace(" ", "")
    to = input(f"Destinatario [{user}]: ").strip() or user
    try:
        patch = config.validate({"smtp_host": host, "smtp_port": int(port), "recipient": to})
    except ValueError as e:
        print("Valore non valido:", e)
        return 2
    if "@" not in user or not pw:
        print("Indirizzo o password mancanti.")
        return 2
    backend = credstore.save(user, pw)
    db.set_settings(patch)
    print(f"Credenziali salvate in: {backend}")
    if backend != credstore.BACKEND_CREDMAN:
        print("ATTENZIONE: Credential Manager non disponibile; uso il file DPAPI di fallback "
              f"in {paths.data_dir()} (legato al tuo utente Windows).")
    print("Nessuna email inviata. Prossimi passi: ccmail install-hooks, ccmail on (e, se vuoi, ccmail test).")
    return 0


def cmd_daemon(a):
    import os
    from . import daemon
    env_port = os.environ.get("CCMAIL_PORT")  # dev/test override; the CLI finds the port via daemon.json
    return daemon.run(foreground=a.foreground, port=int(env_port) if env_port else None)


def cmd_on(a):
    set_enabled(DB(paths.db_path()), True)
    ok = _ensure_daemon()
    print("Notifiche ATTIVE (nessuna email di conferma)." + ("" if ok else " Demone non avviato: partirà al primo evento."))
    return 0


def cmd_off(a):
    set_enabled(DB(paths.db_path()), False)
    print("Notifiche DISATTIVATE (nessuna email di conferma).")
    return 0


def cmd_status(a):
    db = DB(paths.db_path())
    s = db.get_settings()
    st = _api("GET", "/v1/status")
    cred = credstore.describe()
    print("Notifiche:", "ATTIVE" if s["enabled"] else "DISATTIVATE")
    print("Demone:", "in esecuzione" if st else "fermo")
    print("Credenziali:", f"{cred[0]} ({cred[1]})" if cred else "NON configurate (ccmail init)")
    print("Hook installati:", "sì" if installer.is_installed() else "no (ccmail install-hooks)")
    print("Coda:", db.outbox_counts() or "vuota")
    cwd = getattr(a, "cwd", None)
    if cwd:  # per-project switch (web app → Attività); used by the mail-notify skill
        from .db import leaf_name, norm_dir
        on = db.project_enabled(norm_dir(cwd))
        print(f"Progetto «{leaf_name(cwd)}»: notifiche e regole processi lunghi "
              + ("ATTIVE" if on else "DISATTIVATE per questo progetto (interruttore nella webapp)"))
    mode = s["heartbeat_mode"]
    print("Controllo periodico processi in background:",
          {"hook": f"ogni {s['heartbeat_minutes']} min, risveglio gestito dal demone (hook)",
           "skill": f"ogni {s['heartbeat_minutes']} min, a carico di Claude con ScheduleWakeup (skill)",
           "off": "disattivato"}[mode])
    return 0


def cmd_test(a):
    try:
        db = DB(paths.db_path())
        mailer.send_test(db, db.get_settings())
    except mailer.MailError as e:
        print("Errore:", e)
        return 1
    print("Email di prova inviata.")
    return 0


def cmd_open(a):
    if not _ensure_daemon():
        print("Impossibile avviare il demone (vedi ccmail.log).")
        return 1
    _, d = _api("POST", "/v1/open-nonce", {})
    port = json.loads(paths.info_path().read_text(encoding="utf-8"))["port"]
    webbrowser.open(f"http://127.0.0.1:{port}/auth?n={d['nonce']}")
    return 0


def cmd_stop(a):
    print("Demone fermato." if _api("POST", "/v1/shutdown", {}) else "Demone non in esecuzione.")
    return 0


def cmd_install_hooks(a):
    installer.install()
    print(f"Hook installati in {installer.default_settings_path()} (backup creato).")
    if a.autostart:
        print("Avvio automatico:", "configurato" if installer.autostart(True) else "NON riuscito")
    return 0


def cmd_uninstall_hooks(a):
    print(f"Voci rimosse: {installer.uninstall()}")
    if a.autostart:
        installer.autostart(False)
    return 0


def cmd_diagnose(a):
    """Read-only report of what ccmail can see (app metadata, transcripts, links)."""
    from . import diagnostics
    d = diagnostics.collect(DB(paths.db_path()))
    e = d["environment"]
    print("Python:", e["python"], "(Microsoft Store: sì)" if e["store_python"] else "(Microsoft Store: no)")
    print("\nMetadati dell'app desktop (percorsi provati):")
    for r in d["metadata_roots"]:
        print(f"  [{'x' if r['exists'] else ' '}] {r['path']}  ->  {r['chat_files']} chat")
    ib = d.get("inbound", {})
    when = time.strftime("%H:%M:%S", time.localtime(ib["last_poll_at"])) if ib.get("last_poll_at") else "mai"
    print(f"\nRisposta via email: {'attiva' if ib.get('enabled') else 'spenta'}   "
          f"ultimo controllo: {when}   esito: {ib.get('last_status')}")
    print(f"Chat nei file: {d['chats_in_files']}   nel database: {d['chats_in_database']}")
    s = d["sessions"]
    print(f"Sessioni viste: {s['total']}  collegate a una chat: {s['linked_to_chat']}  fuori dall'app: {s['unlinked']}"
          f"  con titolo: {s['with_title']}")
    print("\nProgetti nel database:")
    for p in d["projects"] or []:
        print(f"  - {p['name']}  [{'attivo' if p['enabled'] else 'DISATTIVATO'}]")
    print("\nCartelle di transcript (~/.claude/projects):")
    for f in d["transcripts"]["folders"]:
        print(f"  - {f['folder']}: {f['sessions']} sessioni")
    for w in d["warnings"]:
        print("\nATTENZIONE:", w)
    return 0


def cmd_trace(a):
    from . import tracer
    if a.action == "on":
        installer.trace_install()
        print(f"Tracciamento diagnostico attivo (file: {tracer.trace_file()}).")
        print("Riavvia Claude Code (gli hook si leggono all'avvio), poi esegui un comando in background e "
              "fai finire il processo. Infine: ccmail trace show; a prova finita: ccmail trace off.")
    elif a.action == "off":
        print(f"Voci di tracciamento rimosse: {installer.trace_uninstall()}")
        tf = tracer.trace_file()
        if tf.exists():  # may hold truncated command/output snippets from your projects
            tf.unlink()
            print("File di traccia cancellato.")
    else:
        print("\n".join(tracer.summarize()))
    return 0


def cmd_migrate(a):
    """Remove the legacy prototype hooks. Never opens the prototype's credential file."""
    from pathlib import Path
    legacy_dir = Path.home() / ".claude" / "email-notify"
    was_on = (legacy_dir / "enabled").exists()
    n = installer.uninstall(legacy=True) if installer.has_legacy() else 0
    print(f"Hook del prototipo rimossi dai settings: {n}")
    print("Prototipo era:", "ATTIVO" if was_on else "disattivato")
    print(f"Dopo aver verificato ccmail, cancella tu la cartella {legacy_dir} "
          "(contiene l'App Password in chiaro) e revoca/rigenera l'App Password se vuoi.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="ccmail", description="Email notifications for Claude Code events")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    ini = sub.add_parser("init", help="salva credenziali SMTP (esegui tu, in un tuo terminale)")
    ini.add_argument("--visible", action="store_true", help="mostra la password mentre la digiti")
    ini.set_defaults(fn=cmd_init)
    st = sub.add_parser("status", help="mostra lo stato")
    st.add_argument("--cwd", help="cartella del progetto: mostra anche l'interruttore di quel progetto")
    st.set_defaults(fn=cmd_status)
    for name, fn, h in [("on", cmd_on, "attiva le notifiche (nessuna email)"),
                        ("off", cmd_off, "disattiva le notifiche (nessuna email)"),
                        ("test", cmd_test, "invia UNA email di prova"),
                        ("open", cmd_open, "apri la webapp di configurazione"),
                        ("stop", cmd_stop, "ferma il demone"),
                        ("migrate", cmd_migrate, "rimuove gli hook del prototipo")]:
        sub.add_parser(name, help=h).set_defaults(fn=fn)
    sub.add_parser("diagnose", help="mostra cosa vede ccmail dell'app desktop (sola lettura)").set_defaults(
        fn=cmd_diagnose)
    t = sub.add_parser("trace", help="diagnostica: registra la forma degli eventi hook (on|off|show)")
    t.add_argument("action", choices=["on", "off", "show"])
    t.set_defaults(fn=cmd_trace)
    d = sub.add_parser("daemon", help="avvia il demone")
    d.add_argument("--foreground", action="store_true")
    d.set_defaults(fn=cmd_daemon)
    i = sub.add_parser("install-hooks", help="installa gli hook in settings.json")
    i.add_argument("--autostart", action="store_true", help="avvia il demone al logon (Task Scheduler)")
    i.set_defaults(fn=cmd_install_hooks)
    u = sub.add_parser("uninstall-hooks", help="rimuove gli hook ccmail")
    u.add_argument("--autostart", action="store_true")
    u.set_defaults(fn=cmd_uninstall_hooks)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
