"""Heartbeat timing and reply delivery. One waiter per session; a newer wait or any sign of activity
cancels the older one.

The wake hook (`ccmail wake`, async + asyncRewake on Stop) parks here via POST /v1/heartbeat/wait.
It is released with one of three outcomes:
  - "elapsed":   the heartbeat period passed undisturbed (the daemon may then wake Claude to check tasks);
  - "message":   a verified e-mail reply for this session arrived (the daemon wakes Claude with its text);
  - "cancelled": newer wait, activity in the session, or shutdown (nothing to do).
Exit code 2 of the hook is what turns a "wake" answer into a system reminder that wakes Claude.
"""
import threading
import time


class Heartbeat:
    def __init__(self):
        self._cv = threading.Condition()
        self._gen = {}
        self._activity = {}
        self._mail = {}
        self._parked = {}
        self._closed = False

    def note_activity(self, sid: str) -> None:
        with self._cv:
            self._activity[sid] = self._activity.get(sid, 0) + 1
            self._cv.notify_all()

    def message_arrived(self, sid: str) -> None:
        with self._cv:
            self._mail[sid] = self._mail.get(sid, 0) + 1
            self._cv.notify_all()

    def parked(self) -> dict:
        """sessions whose wake hook is currently waiting here -> since when (epoch seconds)."""
        with self._cv:
            return dict(self._parked)

    def is_parked(self, sid: str) -> bool:
        with self._cv:
            return sid in self._parked

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def wait_for(self, sid: str, seconds: float, listen: bool = False) -> str:
        """Block up to `seconds`; return 'elapsed' | 'message' | 'cancelled'."""
        with self._cv:
            self._gen[sid] = self._gen.get(sid, 0) + 1
            mine = self._gen[sid]
            act = self._activity.get(sid, 0)
            mail = self._mail.get(sid, 0)
            self._cv.notify_all()  # release the waiter this one supersedes right away
            self._parked[sid] = time.time()
            deadline = time.monotonic() + seconds
            try:
                while True:
                    if self._closed or self._gen.get(sid) != mine:
                        return "cancelled"
                    if listen and self._mail.get(sid, 0) != mail:
                        return "message"
                    if self._activity.get(sid, 0) != act:
                        return "cancelled"
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return "elapsed"
                    self._cv.wait(min(left, 30))
            finally:
                if self._gen.get(sid) == mine:  # a newer wait for the same session keeps its own entry
                    self._parked.pop(sid, None)

    def wait(self, sid: str, seconds: float) -> bool:
        """Block up to `seconds`. True only if the full period elapsed undisturbed."""
        return self.wait_for(sid, seconds, listen=False) == "elapsed"


def wake_message(minutes: int, task_ids) -> str:
    ids = ", ".join(task_ids[:5]) or "?"
    return (f"[ccmail] Controllo periodico (ogni {minutes} min): risultano ancora task in background ({ids}). "
            "Interroga ora il loro stato con il comando più economico (output del task o ultime righe del log), "
            "poi rispondi in al massimo 3 righe: stato (in corso/finito/fallito), avanzamento con un numero, "
            "se serve il mio intervento. Non iniziare altro lavoro. Se un task è finito o fallito, chiudi con "
            "il riepilogo di 10-15 righe (esito, cifre chiave, anomalie, file prodotti, cosa fare dopo), "
            "senza valori delle righe di dati riservati.")


def reply_message(texts) -> str:
    """The reminder that carries the user's e-mail reply(ies) into the chat."""
    body = "\n\n---\n".join(t.strip() for t in texts if t and t.strip())
    return ("[ccmail] Messaggio dell'utente ricevuto via email (mittente verificato: è la sua risposta a una mail di "
            "ccmail). Trattalo come un suo messaggio in questa chat e agisci di conseguenza, rispettando i soliti "
            "permessi:\n\n" + body)
