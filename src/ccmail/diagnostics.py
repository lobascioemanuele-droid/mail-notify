"""What ccmail can see of the desktop app and of Claude Code, so problems are diagnosed with facts."""
from pathlib import Path

from . import chats, paths


def _transcripts() -> dict:
    root = paths.claude_dir() / "projects"
    out = {"root": str(root), "exists": root.is_dir(), "folders": []}
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir():
                n = sum(1 for _ in d.glob("*.jsonl"))  # direct children only: sub-agent transcripts are nested
                out["folders"].append({"folder": d.name, "sessions": n})
    return out


def collect(db) -> dict:
    parsed = [c for _, c in chats.read_all()]
    in_db = db.one("SELECT COUNT(*) n FROM chats")["n"]
    total = db.one("SELECT COUNT(*) n FROM sessions")["n"]
    linked = db.one("SELECT COUNT(*) n FROM sessions WHERE chat_id IS NOT NULL")["n"]
    with_title = db.one("SELECT COUNT(*) n FROM sessions WHERE title IS NOT NULL AND title!=''")["n"]
    env = chats.environment()
    roots = chats.root_report()
    warnings = []
    if not any(r["exists"] for r in roots):
        warnings.append("Nessuna cartella dei metadati dell'app trovata: i nomi delle chat arriveranno solo dai "
                        "transcript. Percorsi provati elencati sotto.")
    elif parsed and not in_db:
        warnings.append("I metadati ci sono ma il demone non li ha ancora caricati: riavvialo (mail-notify stop, mail-notify open).")
    if total and linked < total:
        warnings.append(f"{total - linked} sessioni non sono collegate a una chat dell'app "
                        "(avviate da terminale o chat cancellate): compaiono come «fuori dall'app».")
    meta = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM meta WHERE key LIKE 'inbound_%'")}
    st = db.get_settings()
    last = float(meta["inbound_last_poll"]) if meta.get("inbound_last_poll") else None
    return {
        "inbound": {"enabled": bool(st["inbound_enabled"]), "last_poll_at": last,
                    "last_status": meta.get("inbound_last_status") or "nessun controllo ancora eseguito"},
        "environment": env,
        "metadata_roots": roots,
        "chats_in_files": len(parsed),
        "chats_in_database": in_db,
        "sessions": {"total": total, "linked_to_chat": linked, "unlinked": total - linked, "with_title": with_title},
        "projects": [dict(r) for r in db.q("SELECT name, project_dir, enabled FROM projects ORDER BY name")],
        "transcripts": _transcripts(),
        "warnings": warnings,
    }
