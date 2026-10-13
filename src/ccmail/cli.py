"""ccmail command line. `on`, `off`, `init` never send email; only `test` does, on explicit request."""
import argparse
import getpass
import http.client
import json
import os
import socket
import sys
import time
import webbrowser

from . import __version__, auth, config, credstore, hookclient, i18n, installer, mailer, paths, providers
from .db import DB, set_enabled


def _lang():
    try:
        return i18n.resolve(DB(paths.db_path()).get_settings())
    except Exception:
        return i18n.system_language()


def _(it: str, en: str) -> str:
    """Pick the Italian or English text for this machine's language setting."""
    return en if _lang() == "en" else it


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
        if not 200 <= r.status < 300:
            return None  # e.g. 401/403: something else (another data folder's daemon) answers on that port
        return r.status, data
    except (OSError, ValueError):
        return None


def _ensure_daemon() -> bool:
    auth.load_or_create_token()
    if _api("GET", "/v1/status"):
        return True
    hookclient.start_daemon()
    for _i in range(20):
        time.sleep(0.25)
        if _api("GET", "/v1/status"):
            return True
    return False


def cmd_init(a):
    db = DB(paths.db_path())
    cur = db.get_settings()
    user = input(_("Indirizzo email del mittente (es. nome@gmail.com): ",
                   "Sender e-mail address (e.g. name@gmail.com): ")).strip()
    if "@" not in user:
        print(_("Indirizzo non valido.", "Invalid address."))
        return 2
    guess = providers.guess(user)
    print(_("Provider disponibili:", "Available providers:"))
    for i, key in enumerate(providers.ORDER, 1):
        mark = _(" (consigliato)", " (suggested)") if key == guess else ""
        print(f"  {i}. {providers.PROVIDERS[key]['label']}{mark}")
    dflt = providers.ORDER.index(guess) + 1
    raw = input(_(f"Scelta [{dflt}]: ", f"Choice [{dflt}]: ")).strip()
    try:
        key = providers.ORDER[int(raw) - 1] if raw else guess
    except (ValueError, IndexError):
        print(_("Scelta non valida.", "Invalid choice."))
        return 2
    pv = providers.PROVIDERS[key]
    host, port, sec, imap = pv["smtp_host"], pv["smtp_port"], pv["smtp_security"], pv["imap_host"]
    if key == "custom":
        host = input(_("Server SMTP: ", "SMTP server: ")).strip()
        port = input(_("Porta SMTP [465]: ", "SMTP port [465]: ")).strip() or 465
        sec = "starttls" if str(port) == "587" else "ssl"
        imap = input(_("Server IMAP (vuoto = nessuna risposta via email): ",
                       "IMAP server (empty = no reply by e-mail): ")).strip() or cur["imap_host"]
    else:
        print(pv["note"])
        if pv["app_password_url"]:
            print(_("Crea la password per le app qui: ", "Create the app password here: ") + pv["app_password_url"])
        if not pv["tested"]:
            print(_("Nota: questo provider non è ancora stato provato (è previsto in una versione successiva); "
                    "se non funziona usa «Altro provider».",
                    "Note: this provider has not been tested yet (planned for a later release); "
                    "if it fails use «Other provider»."))
    if a.visible:
        pw = input(_("Password per le app (VISIBILE mentre scrivi): ",
                     "App password (VISIBLE while you type): ")).replace(" ", "")
    else:
        pw = getpass.getpass(_("Password per le app (nessun carattere viene mostrato): ",
                               "App password (nothing is shown while you type): ")).replace(" ", "")
    to = input(_(f"Destinatario [{user}]: ", f"Recipient [{user}]: ")).strip() or user
    device = input(_("Nome di questo dispositivo (facoltativo, utile se usi ccmail su più PC): ",
                     "Name of this device (optional, useful if you use mail-notify on several PCs): ")).strip()
    try:
        patch = config.validate({"smtp_host": host, "smtp_port": int(port), "smtp_security": sec,
                                 "imap_host": imap, "recipient": to, "device_name": device})
    except ValueError as e:
        print(_("Valore non valido:", "Invalid value:"), e)
        return 2
    if not pw:
        print(_("Password mancante.", "Password missing."))
        return 2
    backend = credstore.save(user, pw)
    db.set_settings(patch)
    print(_(f"Credenziali salvate in: {backend}", f"Credentials saved in: {backend}"))
    if not credstore.is_secure(backend):
        if backend == credstore.BACKEND_FILE:
            print(_("ATTENZIONE: Credential Manager non disponibile; uso il file DPAPI di fallback "
                    f"in {paths.data_dir()} (legato al tuo utente Windows).",
                    "WARNING: Credential Manager is unavailable; using the DPAPI fallback file "
                    f"in {paths.data_dir()} (bound to your Windows user)."))
        else:
            print(_("ATTENZIONE: nessun archivio sicuro del sistema disponibile (Keychain / Secret Service); "
                    f"la password è in un file riservato al tuo utente (permessi 0600, NON cifrato) in "
                    f"{paths.data_dir()}. Su Linux installa libsecret-tools e riesegui mail-notify init.",
                    "WARNING: no secure system store available (Keychain / Secret Service); the password is in a "
                    f"file readable only by your user (mode 0600, NOT encrypted) in {paths.data_dir()}. "
                    "On Linux install libsecret-tools and run mail-notify init again."))
    print(_("Nessuna email inviata. Prossimi passi: mail-notify install-hooks, mail-notify install-skill, mail-notify on "
            "(e, se vuoi, mail-notify test).",
            "No e-mail sent. Next steps: mail-notify install-hooks, mail-notify install-skill, mail-notify on "
            "(and, if you want, mail-notify test)."))
    return 0


def cmd_daemon(a):
    from . import daemon
    env_port = os.environ.get("CCMAIL_PORT")  # dev/test override; the CLI finds the port via daemon.json
    return daemon.run(foreground=a.foreground, port=int(env_port) if env_port else None)


def cmd_on(a):
    set_enabled(DB(paths.db_path()), True)
    ok = _ensure_daemon()
    print(_("Notifiche ATTIVE (nessuna email di conferma).", "Notifications ON (no confirmation e-mail).")
          + ("" if ok else _(" Demone non avviato: partirà al primo evento.",
                             " Daemon not started: it will start at the first event.")))
    return 0


def cmd_off(a):
    set_enabled(DB(paths.db_path()), False)
    print(_("Notifiche DISATTIVATE (nessuna email di conferma).", "Notifications OFF (no confirmation e-mail)."))
    return 0


def cmd_status(a):
    from . import skillinstall
    db = DB(paths.db_path())
    s = db.get_settings()
    st = _api("GET", "/v1/status")
    cred = credstore.describe()
    en = i18n.resolve(s) == "en"
    print("Notifications:" if en else "Notifiche:",
          ("ON" if en else "ATTIVE") if s["enabled"] else ("OFF" if en else "DISATTIVATE"))
    print("Daemon:" if en else "Demone:", ("running" if en else "in esecuzione") if st else ("stopped" if en else "fermo"))
    if cred:
        print(("Credentials: " if en else "Credenziali: ") + f"{cred[0]} ({cred[1]})")
    else:
        print("Credentials: NOT configured (mail-notify init)" if en else "Credenziali: NON configurate (mail-notify init)")
    print("Hooks installed:" if en else "Hook installati:",
          ("yes" if en else "sì") if installer.is_installed() else "no (mail-notify install-hooks)")
    sk = skillinstall.state()
    if not sk["installed"]:
        print("Skill: not installed (mail-notify install-skill)" if en else "Skill: non installata (mail-notify install-skill)")
    elif not sk["current"]:
        print("Skill: outdated (mail-notify install-skill)" if en else "Skill: non aggiornata (mail-notify install-skill)")
    else:
        print("Skill: " + ("installed" if en else "installata"))
    print("Queue:" if en else "Coda:", db.outbox_counts() or ("empty" if en else "vuota"))
    cwd = getattr(a, "cwd", None)
    if cwd:  # per-project switch (web app → Attività); used by the mail-notify skill
        from .db import leaf_name, norm_dir
        on = db.project_enabled(norm_dir(cwd))
        if en:
            print(f"Project «{leaf_name(cwd)}»: notifications and long-process rules "
                  + ("ON" if on else "OFF for this project (switch in the web app)"))
        else:
            print(f"Progetto «{leaf_name(cwd)}»: notifiche e regole processi lunghi "
                  + ("ATTIVE" if on else "DISATTIVATE per questo progetto (interruttore nella webapp)"))
    mode = s["heartbeat_mode"]
    n = s["heartbeat_minutes"]
    if en:
        txt = {"hook": f"every {n} min, wake-up handled by the daemon (hook)",
               "skill": f"every {n} min, handled by Claude with ScheduleWakeup (skill)", "off": "off"}[mode]
        print("Periodic check of background processes:", txt)
    else:
        txt = {"hook": f"ogni {n} min, risveglio gestito dal demone (hook)",
               "skill": f"ogni {n} min, a carico di Claude con ScheduleWakeup (skill)", "off": "disattivato"}[mode]
        print("Controllo periodico processi in background:", txt)
    return 0


def cmd_test(a):
    try:
        db = DB(paths.db_path())
        mailer.send_test(db, db.get_settings())
    except mailer.MailError as e:
        print(_("Errore:", "Error:"), e)
        return 1
    print(_("Email di prova inviata.", "Test e-mail sent."))
    return 0


def cmd_open(a):
    if not _ensure_daemon():
        print(_("Impossibile avviare il demone (vedi ccmail.log).", "Cannot start the daemon (see ccmail.log)."))
        return 1
    r = _api("POST", "/v1/open-nonce", {})
    if not r:
        print(_("Il demone non risponde (vedi ccmail.log).", "The daemon is not answering (see ccmail.log)."))
        return 1
    _st, d = r
    port = json.loads(paths.info_path().read_text(encoding="utf-8"))["port"]
    webbrowser.open(f"http://127.0.0.1:{port}/auth?n={d['nonce']}")
    return 0


def _ensure_cli() -> None:
    """Create the `mail-notify` launcher next to the other install steps, so nobody has to remember a third one.
    Never fatal and never overwrites a file that is not ours."""
    from . import launcher
    try:
        if launcher.target().exists():  # ours (maybe from another python) or foreign: `install-cli` replaces ours
            return
        path, found = launcher.install()
    except (FileExistsError, OSError):
        return
    note = "" if found else _(f" (la cartella {path.parent} non è nel PATH)", f" (the folder {path.parent} is not on PATH)")
    print(_(f"Comando breve pronto: «{launcher.NAME} open», «{launcher.NAME} status»… in un terminale nuovo.{note}",
            f"Short command ready: «{launcher.NAME} open», «{launcher.NAME} status»… in a new terminal.{note}"))


def cmd_install_cli(a):
    from . import launcher
    try:
        path, found = launcher.install()
    except FileExistsError as e:
        print(_(f"Esiste già un file «{e}» che non è mio: non lo sovrascrivo.",
                f"A file «{e}» already exists and is not mine: I will not overwrite it."))
        return 1
    print(_(f"Comando creato: {path}", f"Command created: {path}"))
    if found:
        print(_(f"Da ora puoi scrivere «{launcher.NAME} open», «{launcher.NAME} status» ... in un terminale NUOVO.",
                f"You can now type «{launcher.NAME} open», «{launcher.NAME} status» ... in a NEW terminal."))
    else:
        print(_(f"Attenzione: la cartella {path.parent} non è nel PATH. Aggiungila al PATH, poi riapri il terminale.",
                f"Note: the folder {path.parent} is not on PATH. Add it to PATH, then reopen the terminal."))
    return 0


def cmd_uninstall_cli(a):
    from . import launcher
    path = launcher.uninstall()
    print(_(f"Comando rimosso: {path}", f"Command removed: {path}") if path
          else _("Nessun comando mio da rimuovere.", "No launcher of mine to remove."))
    return 0


def cmd_stop(a):
    print(_("Demone fermato.", "Daemon stopped.") if _api("POST", "/v1/shutdown", {})
          else _("Demone non in esecuzione.", "Daemon not running."))
    return 0


def cmd_install_hooks(a):
    installer.install()
    print(_(f"Hook installati in {installer.default_settings_path()} (backup creato).",
            f"Hooks installed in {installer.default_settings_path()} (backup created)."))
    print(_("Riavvia Claude Code: gli hook si leggono all'avvio.", "Restart Claude Code: hooks are read at startup."))
    if a.autostart:
        how = installer.autostart(True)
        print(_("Avvio automatico:", "Autostart:") + " "
              + (_("configurato", "configured") if how else _("NON riuscito", "FAILED")))
    _ensure_cli()
    return 0


def cmd_uninstall_hooks(a):
    removed = installer.uninstall()  # once: a second call would report 0 in the second language
    print(_(f"Voci rimosse: {removed}", f"Entries removed: {removed}"))
    if a.autostart:
        installer.autostart(False)
    return 0


def cmd_setup(a):
    """Everything that is not the password: the hooks, the skill and the short command (and, on request, the start
    at logon). `init` stays separate on purpose: the password is typed by the user, in their own terminal."""
    class _Opts:
        autostart = bool(getattr(a, "autostart", False))

    cmd_install_hooks(_Opts)
    cmd_install_skill(_Opts)
    print()
    if not credstore.describe():
        print(_("Prossimo passo: «mail-notify init» (indirizzo e password per le app), poi «mail-notify on» e "
                "«mail-notify test».",
                "Next step: «mail-notify init» (address and app password), then «mail-notify on» and "
                "«mail-notify test»."))
    else:
        print(_("Credenziali già presenti. Prossimo passo: «mail-notify on» e «mail-notify test».",
                "Credentials already set up. Next step: «mail-notify on» and «mail-notify test»."))
    print(_("Riavvia Claude Code: hook e skill si leggono all'avvio.",
            "Restart Claude Code: hooks and skill are read at startup."))
    return 0


def cmd_install_skill(a):
    from . import skillinstall
    r = skillinstall.install()
    print(_(f"Skill «mail-notify» installata in {r['path']}.", f"Skill «mail-notify» installed in {r['path']}."))
    if r["backup"]:
        print(_(f"Il file precedente non era di ccmail: salvato in {r['backup']}.",
                f"The previous file was not ccmail's: saved as {r['backup']}."))
    print(_("Riavvia Claude Code per vederla (/mail-notify on|off|status|test|open).",
            "Restart Claude Code to see it (/mail-notify on|off|status|test|open)."))
    _ensure_cli()
    return 0


def cmd_uninstall_skill(a):
    from . import skillinstall
    print(_("Skill rimossa.", "Skill removed.") if skillinstall.uninstall()
          else _("Nessuna skill di ccmail da rimuovere.", "No ccmail skill to remove."))
    return 0


def _doctor_checks(network: bool):
    """[(level, title, detail, hint)], level in ok|warn|bad. Never prints secrets."""
    from . import autostart, skillinstall
    out = []

    def add(level, title, detail="", hint=""):
        out.append((level, title, detail, hint))

    db = DB(paths.db_path())
    s = db.get_settings()
    en = i18n.resolve(s) == "en"

    def L(it, eng):
        return eng if en else it

    add("ok", L("Sistema", "System"), f"{sys.platform}, Python {sys.version.split()[0]}, ccmail {__version__}")
    add("ok", L("Cartella dati", "Data folder"), str(paths.data_dir()))
    try:
        cred = credstore.describe()
    except Exception as e:
        cred = None
        add("bad", L("Credenziali illeggibili", "Credentials unreadable"), type(e).__name__, "mail-notify init")
    else:
        if not cred:
            add("bad", L("Credenziali non configurate", "Credentials not configured"), "", "mail-notify init")
        elif credstore.is_secure(cred[1]):
            add("ok", L("Credenziali", "Credentials"), f"{cred[0]} ({cred[1]})")
        else:
            add("warn", L("Credenziali in un file di riserva", "Credentials in a fallback file"),
                f"{cred[0]} ({cred[1]})",
                L("installa l'archivio sicuro del sistema e riesegui mail-notify init",
                  "install the system secure store and run mail-notify init again"))
    if installer.is_installed():
        add("ok", L("Hook installati", "Hooks installed"), str(installer.default_settings_path()))
        exes = installer.hook_interpreters()
        missing = [e for e in exes if not os.path.exists(e)]
        if missing:
            add("bad", L("Hook che puntano a un Python inesistente", "Hooks pointing to a missing Python"),
                ", ".join(missing), "mail-notify install-hooks")
        elif exes and sys.executable not in exes:
            add("warn", L("Gli hook usano un altro Python", "Hooks use a different Python"), ", ".join(exes),
                L("se hai reinstallato ccmail: mail-notify install-hooks", "if you reinstalled ccmail: mail-notify install-hooks"))
    else:
        add("bad", L("Hook non installati", "Hooks not installed"), "", "mail-notify install-hooks")
    sk = skillinstall.state()
    if not sk["installed"]:
        add("warn", L("Skill non installata", "Skill not installed"), "", "mail-notify install-skill")
    elif not sk["current"]:
        add("warn", L("Skill non aggiornata", "Skill outdated"), sk["path"], "mail-notify install-skill")
    else:
        add("ok", "Skill", sk["path"])
    try:
        enabled = autostart.is_enabled()
    except Exception:
        enabled = False
    add("ok" if enabled else "warn", L("Avvio automatico", "Autostart"),
        L("attivo", "enabled") if enabled else L("non attivo", "not enabled"),
        "" if enabled else "mail-notify install-hooks --autostart")
    st = _api("GET", "/v1/status")
    add("ok" if st else "warn", L("Demone", "Daemon"), L("in esecuzione", "running") if st else L("fermo", "stopped"),
        "" if st else "mail-notify on")
    if network:
        for label, host, port in (("SMTP", s["smtp_host"], s["smtp_port"]), ("IMAP", s["imap_host"], 993)):
            try:
                socket.create_connection((host, int(port)), timeout=6).close()
                add("ok", L(f"Rete {label}", f"Network {label}"), f"{host}:{port}")
            except OSError as e:
                add("bad", L(f"Rete {label}", f"Network {label}"), f"{host}:{port} ({type(e).__name__})")
    return out


def cmd_doctor(a):
    marks = {"ok": "[ OK ]", "warn": "[WARN]", "bad": "[FAIL]"}
    worst = 0
    for level, title, detail, hint in _doctor_checks(a.network):
        print(f"{marks[level]} {title}" + (f": {detail}" if detail else ""))
        if hint:
            print(f"       -> {hint}")
        worst = max(worst, {"ok": 0, "warn": 0, "bad": 1}[level])
    if not a.network:
        print(_("(usa --network per provare anche la raggiungibilità dei server di posta)",
                "(use --network to also probe the mail servers)"))
    return worst


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
    print(f"\nCartelle di transcript ({d['transcripts']['root']}):")
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
              "fai finire il processo. Infine: mail-notify trace show; a prova finita: mail-notify trace off.")
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
    legacy_dir = paths.claude_dir() / "email-notify"
    was_on = (legacy_dir / "enabled").exists()
    n = installer.uninstall(legacy=True) if installer.has_legacy() else 0
    print(f"Hook del prototipo rimossi dai settings: {n}")
    print("Prototipo era:", "ATTIVO" if was_on else "disattivato")
    print(f"Dopo aver verificato ccmail, cancella tu la cartella {legacy_dir} "
          "(contiene l'App Password in chiaro) e revoca/rigenera l'App Password se vuoi.")
    return 0


def main(argv=None):
    name0 = os.path.basename(sys.argv[0]).lower()
    prog = os.environ.get("CCMAIL_PROG") or ("mail-notify" if name0.startswith("mail-notify") else "ccmail")
    p = argparse.ArgumentParser(prog=prog, description="Email notifications for Claude Code events")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    ini = sub.add_parser("init", help="set up the sender account and password (run it yourself, in your terminal)")
    ini.add_argument("--visible", action="store_true", help="show the password while you type it")
    ini.set_defaults(fn=cmd_init)
    st = sub.add_parser("status", help="show the state")
    st.add_argument("--cwd", help="project folder: also show that project's switch")
    st.set_defaults(fn=cmd_status)
    for name, fn, h in [("on", cmd_on, "turn notifications on (no mail)"),
                        ("off", cmd_off, "turn notifications off (no mail)"),
                        ("test", cmd_test, "send ONE test e-mail"),
                        ("open", cmd_open, "open the settings web app"),
                        ("stop", cmd_stop, "stop the daemon"),
                        ("install-cli", cmd_install_cli, "create the short «mail-notify» command (on PATH)"),
                        ("uninstall-cli", cmd_uninstall_cli, "remove the «mail-notify» command"),
                        ("install-skill", cmd_install_skill, "install the mail-notify skill for Claude Code"),
                        ("uninstall-skill", cmd_uninstall_skill, "remove the mail-notify skill"),
                        ("migrate", cmd_migrate, "remove the old prototype's hooks")]:
        sub.add_parser(name, help=h).set_defaults(fn=fn)
    dr = sub.add_parser("doctor", help="check the installation and say what to fix (prints no secrets)")
    dr.add_argument("--network", action="store_true", help="also probe the mail servers")
    dr.set_defaults(fn=cmd_doctor)
    sub.add_parser("diagnose", help="show what ccmail sees of the desktop app (read only)").set_defaults(
        fn=cmd_diagnose)
    t = sub.add_parser("trace", help="diagnostics: record the shape of hook events (on|off|show)")
    t.add_argument("action", choices=["on", "off", "show"])
    t.set_defaults(fn=cmd_trace)
    d = sub.add_parser("daemon", help="start the daemon")
    d.add_argument("--foreground", action="store_true")
    d.set_defaults(fn=cmd_daemon)
    su = sub.add_parser("setup", help="install the hooks, the skill and the short command in one go "
                                       "(run `init` separately: it asks for the password)")
    su.add_argument("--autostart", action="store_true", help="also start the daemon at logon")
    su.set_defaults(fn=cmd_setup)
    i = sub.add_parser("install-hooks", help="install the hooks in Claude Code's settings.json")
    i.add_argument("--autostart", action="store_true", help="start the daemon at logon")
    i.set_defaults(fn=cmd_install_hooks)
    u = sub.add_parser("uninstall-hooks", help="remove the ccmail hooks")
    u.add_argument("--autostart", action="store_true")
    u.set_defaults(fn=cmd_uninstall_hooks)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
