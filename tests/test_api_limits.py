import http.client
import json
import threading
import time
import unittest

from ccmail import auth, policy
from ccmail.server import Server
from ccmail.worker import Worker
from helpers import TmpHome

ROOT = "C:/w/Alpha"


class LimitTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.token = auth.load_or_create_token()
        self.srv = Server(("127.0.0.1", 0), self.db, auth.Auth(self.token), Worker(self.db), lambda: None)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        now_ms = int(time.time() * 1000)
        for i in range(8):  # 8 live chats, the newest is i=7
            self.add_chat(f"local_{i}", f"cli-{i}", f"Chat {i}", ROOT, last_ms=now_ms - (8 - i) * 60_000)
        self.add_chat("local_old", "cli-old", "Vecchia", ROOT, archived=True, last_ms=now_ms - 99_000_000)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers={"Authorization": f"Bearer {self.token}"})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_default_is_five_newest_per_project(self):
        st, d = self.get("/v1/projects")
        p = d["projects"][0]
        self.assertEqual(d["chat_limit"], 5)
        self.assertEqual([c["title"] for c in p["chats"]], [f"Chat {i}" for i in (7, 6, 5, 4, 3)])
        self.assertEqual((p["chats_total"], p["chats_hidden"]), (8, 3))
        self.assertEqual([c["title"] for c in p["archived"]], ["Vecchia"])  # not counted in the limit

    def test_all_and_custom_limits_and_validation(self):
        self.assertEqual(len(self.get("/v1/projects?chats=0")[1]["projects"][0]["chats"]), 8)
        self.assertEqual(len(self.get("/v1/projects?chats=2")[1]["projects"][0]["chats"]), 2)
        self.assertEqual(self.get("/v1/projects?chats=abc")[0], 400)
        self.assertEqual(self.get("/v1/projects?chats=999")[0], 400)

    def test_ghost_subfolder_does_not_add_a_project(self):
        tp = "C:/u/.claude/projects/C--w-Alpha/s.jsonl"
        policy.handle_event(self.db, {"hook_event_name": "UserPromptSubmit", "session_id": "cli-3",
                                      "cwd": ROOT + "/uscite", "transcript_path": tp}, now=time.time())
        names = [p["name"] for p in self.get("/v1/projects?chats=0")[1]["projects"]]
        self.assertEqual(names, ["Alpha"])

    def test_diagnostics_shape(self):
        st, d = self.get("/v1/diagnostics")
        self.assertEqual(st, 200)
        self.assertEqual(d["chats_in_files"], 9)
        self.assertEqual(d["chats_in_database"], 9)
        self.assertTrue(d["metadata_roots"][0]["exists"])
        self.assertEqual(set(d["sessions"]), {"total", "linked_to_chat", "unlinked", "with_title"})
        self.assertIn("transcripts", d)


if __name__ == "__main__":
    unittest.main()
