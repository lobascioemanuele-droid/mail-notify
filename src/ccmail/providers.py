"""Mail provider presets: SMTP/IMAP hosts and where to create an app password.

Only Gmail is exercised end to end by the author; the other entries follow the providers' published settings and
are marked as such in the docs. "custom" lets the user type everything.
"""

PROVIDERS = {
    "gmail": {
        "label": "Gmail", "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_security": "ssl",
        "imap_host": "imap.gmail.com", "tested": True,
        "app_password_url": "https://myaccount.google.com/apppasswords",
        "note": "Serve la verifica in due passaggi e una password per le app di 16 lettere.",
    },
    "outlook": {
        "label": "Outlook / Microsoft 365", "smtp_host": "smtp-mail.outlook.com", "smtp_port": 587,
        "smtp_security": "starttls", "imap_host": "outlook.office365.com", "tested": False,
        "app_password_url": "https://account.live.com/proofs/AppPassword",
        "note": "Account personali: password per le app. Molti account aziendali hanno l'accesso con password "
                "disattivato dall'amministratore e non funzioneranno.",
    },
    "icloud": {
        "label": "iCloud Mail", "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_security": "starttls",
        "imap_host": "imap.mail.me.com", "tested": False,
        "app_password_url": "https://account.apple.com/account/manage",
        "note": "Serve una password specifica per app; il mittente è l'indirizzo iCloud.",
    },
    "yahoo": {
        "label": "Yahoo Mail", "smtp_host": "smtp.mail.yahoo.com", "smtp_port": 465, "smtp_security": "ssl",
        "imap_host": "imap.mail.yahoo.com", "tested": False,
        "app_password_url": "https://login.yahoo.com/account/security",
        "note": "Genera una password per le app dalle impostazioni di sicurezza dell'account.",
    },
    "custom": {
        "label": "Altro provider", "smtp_host": "", "smtp_port": 465, "smtp_security": "ssl", "imap_host": "",
        "tested": False, "app_password_url": "", "note": "Inserisci a mano server e porta.",
    },
}
ORDER = ["gmail", "outlook", "icloud", "yahoo", "custom"]


def guess(address: str) -> str:
    """Best preset for an e-mail address, by its domain; 'custom' if unknown."""
    dom = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    if dom in ("gmail.com", "googlemail.com"):
        return "gmail"
    if dom in ("outlook.com", "hotmail.com", "live.com", "msn.com") or dom.endswith(".onmicrosoft.com"):
        return "outlook"
    if dom in ("icloud.com", "me.com", "mac.com"):
        return "icloud"
    if dom.startswith("yahoo.") or dom in ("ymail.com", "rocketmail.com"):
        return "yahoo"
    return "custom"
