import http.client
import io
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from ccmail import auth, hookclient, installer
from ccmail.server import Server
from ccmail.worker import Worker
from helpers import TmpHome


class InTurnDeliveryTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.token = auth.load_or_create_token()
        self.srv = Server(("127.0.0.1", 0), self.db, auth.Auth(self.token), Worker(self.db), lambda: None)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.enable(0)
        self.db.set_settings({"inbound_enabled": True, "inbound_to_claude": True})
        self.db.touch_session("s1", "C:/w/demo", time.time())

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def post(self, event, sid="s1"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/v1/events", body=json.dumps({"hook_event_name": event, "session_id": sid, "cwd": "C:/w/demo"}),
                  headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        r = c.getresponse()
        try:
            return json.loads(r.read())
        finally:
            c.close()

    def test_reply_reaches_a_working_chat_at_the_next_tool_batch(self):
        self.db.add_inbound_message("s1", "c:/w/demo", "Usa il fold 3.", time.time())
        out = self.post("PostToolBatch")
        self.assertIn("Usa il fold 3.", out["context"])
        self.assertIn("[ccmail]", out["context"])
        self.assertNotIn("context", self.post("PostToolBatch"))     # delivered once only
        e = self.db.one("SELECT summary FROM events WHERE event='InboundDelivered'")
        self.assertIn("PostToolBatch", e["summary"])

    def test_it_also_rides_on_the_next_user_prompt(self):
        self.db.add_inbound_message("s1", "c:/w/demo", "Ricordati dei test.", time.time())
        self.assertIn("Ricordati dei test.", self.post("UserPromptSubmit")["context"])

    def test_other_sessions_and_other_events_are_untouched(self):
        self.db.add_inbound_message("s1", "c:/w/demo", "solo per s1", time.time())
        self.assertNotIn("context", self.post("PostToolBatch", sid="s2"))
        self.assertNotIn("context", self.post("Stop"))
        self.assertEqual(len(self.db.pending_inbound("s1", time.time())), 1)

    def test_nothing_is_delivered_when_the_feature_is_off(self):
        self.db.set_settings({"inbound_to_claude": False})
        self.db.add_inbound_message("s1", "c:/w/demo", "x", time.time())
        self.assertNotIn("context", self.post("PostToolBatch"))


class HookOutputTests(unittest.TestCase):
    def run_emit(self, payload, answer):
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            hookclient._emit_context(payload, answer)
        return buf.getvalue()

    def test_prints_the_json_claude_code_expects(self):
        out = json.loads(self.run_emit({"hook_event_name": "PostToolBatch"}, {"context": "ciao àè"}))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolBatch")
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], "ciao àè")

    def test_silent_when_nothing_to_say_or_for_other_events(self):
        self.assertEqual(self.run_emit({"hook_event_name": "PostToolBatch"}, {}), "")
        self.assertEqual(self.run_emit({"hook_event_name": "Stop"}, {"context": "x"}), "")


class InstallerBatchHookTests(TmpHome):
    def test_post_tool_batch_hook_is_synchronous_and_old_install_is_flagged(self):
        f = Path(self._tmp.name) / "settings.json"
        installer.install(f)
        d = json.loads(f.read_text(encoding="utf-8"))
        h = d["hooks"]["PostToolBatch"][0]["hooks"][0]
        self.assertFalse(h.get("async"))
        self.assertTrue(installer.is_installed(f))
        h["async"] = True  # what versions up to 0.5 wrote
        f.write_text(json.dumps(d), encoding="utf-8")
        self.assertFalse(installer.is_installed(f))   # status then tells the user to re-run install-hooks


if __name__ == "__main__":
    unittest.main()
