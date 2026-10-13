"""SMTP sending. Error messages are sanitised: they never contain credentials or server replies verbatim."""
import smtplib
import socket
import ssl
from email.message import EmailMessage

from . import credstore, render


class MailError(Exception):
    pass


def build_message(sender: str, to: str, subject: str, body: str, html: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = render.clean_line(subject, 150)
    msg.set_content(body)
    if html:  # the plain-text part above is unchanged; mail clients that show HTML prefer this alternative
        msg.add_alternative(html, subtype="html")
    return msg


def send_test(db, settings: dict) -> None:
    """Send the explicit test mail and record it in the outbox (so the app can show it as delivered).
    Raises MailError on failure; the failure is recorded too. Test rows never count against the cap."""
    import time
    subject, body = "[Claude Code] Email di prova", "Configurazione SMTP funzionante.\n"
    now = time.time()
    try:
        send(settings, subject, body)
    except MailError as e:
        db.x("INSERT INTO outbox(created_at, subject, body, status, attempts, last_error, kind) "
             "VALUES(?,?,?,?,?,?,?)", (now, subject, body, "failed", 1, str(e), "test"))
        raise
    db.x("INSERT INTO outbox(created_at, subject, body, status, attempts, sent_at, kind) "
         "VALUES(?,?,?,?,?,?,?)", (now, subject, body, "sent", 1, time.time(), "test"))


def send(settings: dict, subject: str, body: str, timeout: int = 20, full_text: str = "") -> None:
    cred = credstore.load()
    if not cred:
        raise MailError("credenziali non configurate: esegui 'mail-notify init'")
    user, password, _ = cred
    to = settings.get("recipient") or user
    html = ""
    if settings.get("html_mail"):
        from . import htmlmail
        try:
            html = htmlmail.render(subject, body, settings, to, full_text)
        except Exception:  # a bug in the optional HTML part must never keep a mail from going out
            html = ""
    msg = build_message(user, to, subject, body, html)
    host, port = settings["smtp_host"], settings["smtp_port"]
    ctx = ssl.create_default_context()
    try:
        if settings["smtp_security"] == "ssl":
            s = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
        else:
            s = smtplib.SMTP(host, port, timeout=timeout)
            s.starttls(context=ctx)
        with s:
            s.login(user, password)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError("autenticazione SMTP rifiutata (controlla l'App Password)") from None
    except (smtplib.SMTPException, ssl.SSLError, socket.timeout, OSError) as e:
        raise MailError(f"invio fallito: {type(e).__name__}") from None
