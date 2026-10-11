import http.client
import json
import threading
import time
import unittest

from ccmail import activity, auth, chats, policy
from ccmail.server import Server
from ccmail.worker import Worker
from helpers import TmpHome

T0 = 1_800_000_000.0


def ev(name, sid="cli-1", cwd="C:/w/progetto/sub", **kw):
    return {"hook_event_name": name, "session_id": sid, "cwd": cwd, **kw}


class ChatLinkTests(TmpHome):
    def test_scan_reads_only_what_it_needs_and_links_the_session(self):
        self.add_chat(title="Revisione  report   Acme", root="C:\\w\\Progetto")
        policy.handle_event(self.db, ev("UserPromptSubmit"), now=T0)
        s = self.db.one("SELECT * FROM sessions WHERE session_id='cli-1'")
        self.assertEqual(s["chat_id"], "local_aaaa")
        self.assertEqual(s["project"], "Progetto")              # chat's project, not the 'sub' cwd
        self.assertEqual(s["project_dir"], "c:/w/progetto")
        self.assertEqual(self.db.chat_title(s), "Revisione report Acme")

    def test_session_started_before_the_chat_file_is_linked_later(self):
        policy.handle_event(self.db, ev("UserPromptSubmit"), now=T0)
        self.assertIsNone(self.db.one("SELECT chat_id FROM sessions")["chat_id"])
        self.add_chat()
        self.assertEqual(self.db.one("SELECT chat_id FROM sessions")["chat_id"], "local_aaaa")

    def test_missing_or_broken_metadata_fails_soft(self):
        self.assertEqual(chats.scan(self.db, force=True), 0)       # folder absent
        import os
        d = os.path.join(self.sessions_dir, "a", "b")
        os.makedirs(d)
        open(os.path.join(d, "local_bad.json"), "w").write("{not json")
        open(os.path.join(d, "local_other.json"), "w").write('{"x": 1}')
        self.assertEqual(chats.scan(self.db, force=True), 0)

    def test_mail_uses_chat_name_instead_of_session_id(self):
        self.add_chat(title="Pipeline uscite")
        self.enable(T0 - 9999)
        policy.handle_event(self.db, ev("UserPromptSubmit"), now=T0)
        policy.handle_event(self.db, ev("Stop", last_assistant_message="Fatto."), now=T0 + 400)
        body = self.db.q("SELECT body FROM outbox")[0]["body"]
        self.assertIn("Chat: Pipeline uscite", body)
        self.assertNotIn("Sessione:", body)
        self.assertIn("Progetto: progetto", body)

    def test_unknown_chat_falls_back_to_session_id(self):
        self.enable(T0 - 9999)
        policy.handle_event(self.db, ev("UserPromptSubmit", sid="zzzzzzzz-1111"), now=T0)
        policy.handle_event(self.db, ev("Stop", sid="zzzzzzzz-1111", last_assistant_message="ok"), now=T0 + 400)
        self.assertIn("Sessione: zzzzzzzz\n", self.db.q("SELECT body FROM outbox")[0]["body"])


class ProjectSwitchTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.enable(T0 - 9999)
        self.add_chat(title="Chat A")

    def long_turn(self, t):
        policy.handle_event(self.db, ev("UserPromptSubmit"), now=t)
        return policy.handle_event(self.db, ev("Stop", last_assistant_message="ok"), now=t + 400)

    def test_project_off_blocks_mail_but_keeps_recording(self):
        self.db.set_project_enabled("C:/W/Progetto", "Progetto", False, T0)
        self.assertEqual(self.long_turn(T0), "skip:project_off")
        self.assertEqual(self.db.q("SELECT * FROM outbox"), [])
        self.assertEqual(len(self.db.q("SELECT * FROM events WHERE event='Stop'")), 1)
        self.db.set_project_enabled("c:/w/progetto/", "Progetto", True, T0 + 1)
        self.assertEqual(self.long_turn(T0 + 1000), "queued")

    def test_project_off_is_not_masked_by_the_post_toggle_silence(self):
        self.db.set_settings({"toggled_at": T0 + 390, "mute_after_toggle_s": 60})  # inside the mute window
        self.db.set_project_enabled("c:/w/progetto", "Progetto", False, T0)
        self.assertEqual(self.long_turn(T0), "skip:project_off")

    def test_simulator_honours_project_switch_and_chat_name(self):
        policy.handle_event(self.db, ev("UserPromptSubmit"), now=T0)
        r = policy.simulate(self.db, "Stop", 600, "Fatto", session_id="cli-1", now=T0 + 9999)
        self.assertTrue(r["would_send"])
        self.assertIn("Chat: Chat A", r["body"])
        self.db.set_project_enabled("c:/w/progetto", "Progetto", False, T0)
        self.assertEqual(policy.simulate(self.db, "Stop", 600, "Fatto", session_id="cli-1",
                                         now=T0 + 9999)["reason"], "project_off")

    def test_other_projects_are_unaffected(self):
        self.db.set_project_enabled("c:/w/progetto", "Progetto", False, T0)
        policy.handle_event(self.db, ev("UserPromptSubmit", sid="s2", cwd="C:/w/altro"), now=T0)
        self.assertEqual(policy.handle_event(self.db, ev("Stop", sid="s2", cwd="C:/w/altro",
                                                         last_assistant_message="x"), now=T0 + 400), "queued")


class StateTests(unittest.TestCase):
    now = T0

    def st(self, sess, chat=None):
        return activity.display_state(sess, chat, self.now)[0]

    def sess(self, **kw):
        base = {"state": "idle", "last_event_at": self.now - 60, "bg_tasks": 0, "heartbeat_pending": 0}
        return {**base, **kw}

    def test_waiting_only_when_recent_and_nothing_else_explains_it(self):
        self.assertEqual(self.st(self.sess()), "waiting")
        self.assertEqual(self.st(self.sess(last_event_at=self.now - 7 * 3600)), "quiet")  # was a lie before
        self.assertEqual(self.st(self.sess(bg_tasks=2)), "bg")
        self.assertEqual(self.st(self.sess(heartbeat_pending=1, bg_tasks=1)), "checking")
        self.assertEqual(self.st(self.sess(state="ended")), "ended")

    def test_app_activity_newer_than_hooks_means_uncertain(self):
        chat = {"last_activity": self.now - 10}
        self.assertEqual(self.st(self.sess(last_event_at=self.now - 600), chat), "unknown")

    def test_working_and_stale(self):
        self.assertEqual(self.st(self.sess(state="active")), "working")
        self.assertEqual(self.st(self.sess(state="active", last_event_at=self.now - 3600)), "stale")

    def test_old_background_knowledge_is_not_presented_as_current(self):
        s = self.sess(bg_tasks=2, last_event_at=self.now - 8 * 3600)
        code, label, detail = activity.display_state(s, None, self.now)
        self.assertEqual(code, "quiet")
        self.assertIn("ultimo stato noto", detail)

    def test_bg_detail_singular_and_plural(self):
        one = activity.display_state(self.sess(bg_tasks=1), None, self.now)[2]
        many = activity.display_state(self.sess(bg_tasks=3), None, self.now)[2]
        self.assertTrue(one.startswith("1 processo in corso"))
        self.assertTrue(many.startswith("3 processi in corso"))

    def test_archived_and_no_events(self):
        self.assertEqual(self.st(None), "none")
        self.assertEqual(self.st(self.sess(), {"archived": 1}), "archived")


class ApiTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.token = auth.load_or_create_token()
        self.srv = Server(("127.0.0.1", 0), self.db, auth.Auth(self.token), Worker(self.db), lambda: None)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_tree_groups_chats_under_projects_with_latest_session(self):
        self.add_chat("local_1", "cli-old", "Chat uno", "C:/w/P1", last_ms=1_000_000_000_000)
        self.add_chat("local_2", "cli-2", "Chat due", "C:/w/P1", last_ms=2_000_000_000_000)
        self.add_chat("local_3", "cli-3", "Altro", "C:/w/P2", last_ms=1_500_000_000_000)
        now = time.time()
        policy.handle_event(self.db, ev("UserPromptSubmit", sid="cli-old", cwd="C:/w/P1"), now=now - 500)
        policy.handle_event(self.db, ev("UserPromptSubmit", sid="cli-1b", cwd="C:/w/P1"), now=now - 400)  # CLI
        self.db.set_session("cli-old", chat_id="local_1")
        # the chat later continued under a new session id: the latest one is the one shown
        self.add_chat("local_1", "cli-new", "Chat uno", "C:/w/P1", last_ms=int((now - 5) * 1000))
        policy.handle_event(self.db, ev("UserPromptSubmit", sid="cli-new", cwd="C:/w/P1"), now=now - 5)
        st, d = self.call("GET", "/v1/projects")
        self.assertEqual(st, 200)
        self.assertTrue(d["app_metadata"])
        names = [p["name"] for p in d["projects"]]
        self.assertEqual(names, ["P1", "P2"])  # most recently active first
        p1 = d["projects"][0]
        titles = [c["title"] for c in p1["chats"]]
        self.assertIn("Chat uno", titles)
        uno = next(c for c in p1["chats"] if c["title"] == "Chat uno")
        self.assertEqual(uno["session"]["session_id"], "cli-new")
        self.assertEqual(uno["sessions_seen"], 2)
        self.assertEqual(uno["state"], "working")
        self.assertTrue(any(c["chat_id"] is None for c in p1["chats"]))  # the CLI-only session
        self.assertTrue(all(c["enabled"] is not None for c in [p1]) if False else p1["enabled"])

    def test_project_switch_roundtrip_and_validation(self):
        self.add_chat("local_1", "cli-1", "Chat", "C:/w/P1")
        st, d = self.call("PUT", "/v1/projects/enabled", {"project_dir": "C:/w/P1", "enabled": False})
        self.assertEqual((st, d["enabled"]), (200, False))
        self.assertFalse(self.call("GET", "/v1/projects")[1]["projects"][0]["enabled"])
        self.assertEqual(self.call("PUT", "/v1/projects/enabled", {"project_dir": "C:/nope", "enabled": True})[0], 400)
        self.assertEqual(self.call("PUT", "/v1/projects/enabled", {"project_dir": "C:/w/P1", "enabled": "x"})[0], 400)

    def test_wake_not_issued_for_disabled_project(self):
        from unittest import mock
        from ccmail.heartbeat import Heartbeat
        self.enable(0)
        self.add_chat("local_1", "cli-1", "Chat", "C:/w/P1")
        policy.handle_event(self.db, ev("Stop", cwd="C:/w/P1", background_tasks=[{"id": "b"}]), now=T0)
        self.db.set_project_enabled("C:/w/P1", "P1", False, T0)
        with mock.patch.object(Heartbeat, "wait_for", return_value="elapsed"):
            st, d = self.call("POST", "/v1/heartbeat/wait", {"session_id": "cli-1", "background_tasks": [{"id": "b"}]})
        self.assertFalse(d["wake"])


if __name__ == "__main__":
    unittest.main()
