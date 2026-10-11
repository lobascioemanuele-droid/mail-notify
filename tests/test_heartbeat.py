import http.client
import json
import sys
import threading
import time
import unittest
from unittest import mock

from ccmail import auth, paths, wakeclient
from ccmail.heartbeat import Heartbeat
from ccmail.server import Server
from ccmail.worker import Worker
from helpers import TmpHome


class HeartbeatTimingTests(unittest.TestCase):
    def test_elapses_when_undisturbed(self):
        self.assertTrue(Heartbeat().wait("s", 0.15))

    def test_activity_cancels(self):
        hb = Heartbeat()
        threading.Timer(0.1, hb.note_activity, ["s"]).start()
        self.assertFalse(hb.wait("s", 5))

    def test_activity_on_other_session_does_not_cancel(self):
        hb = Heartbeat()
        threading.Timer(0.05, hb.note_activity, ["other"]).start()
        self.assertTrue(hb.wait("s", 0.3))

    def test_newer_wait_supersedes_older(self):
        hb = Heartbeat()
        res = []
        t = threading.Thread(target=lambda: res.append(hb.wait("s", 5)))
        t.start()
        time.sleep(0.1)
        threading.Thread(target=lambda: hb.wait("s", 0.2)).start()
        t.join(2)
        self.assertEqual(res, [False])

    def test_close_releases_waiters(self):
        hb = Heartbeat()
        threading.Timer(0.1, hb.close).start()
        self.assertFalse(hb.wait("s", 5))


class WaitEndpointTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.token = auth.load_or_create_token()
        self.srv = Server(("127.0.0.1", 0), self.db, auth.Auth(self.token), Worker(self.db), lambda: None)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.enable(0)
        self.db.touch_session("s1", "C:/w/demo", time.time())
        self.db.set_session("s1", state="idle")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def wait(self, tasks=({"id": "b1"},)):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = json.dumps({"session_id": "s1", "background_tasks": list(tasks)})
        c.request("POST", "/v1/heartbeat/wait", body=body,
                  headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        r = c.getresponse()
        try:
            return json.loads(r.read())
        finally:
            c.close()

    def test_wake_when_period_elapses_and_session_idle(self):
        with mock.patch.object(Heartbeat, "wait_for", return_value="elapsed"):
            r = self.wait()
        self.assertTrue(r["wake"])
        self.assertIn("[ccmail]", r["message"])
        self.assertIn("b1", r["message"])
        self.assertEqual(self.db.one("SELECT heartbeat_pending p FROM sessions")["p"], 1)

    def test_no_wake_without_tasks_or_when_off_or_snoozed_or_busy(self):
        with mock.patch.object(Heartbeat, "wait_for", return_value="elapsed"):
            self.assertFalse(self.wait(tasks=())["wake"])
            self.db.set_settings({"heartbeat_mode": "off"})
            self.assertFalse(self.wait()["wake"])
            self.db.set_settings({"heartbeat_mode": "skill"})
            self.assertFalse(self.wait()["wake"])  # skill mode: Claude self-schedules, daemon stays quiet
            self.db.set_settings({"heartbeat_mode": "hook", "snooze_until": time.time() + 600})
            self.assertFalse(self.wait()["wake"])
            self.db.set_settings({"snooze_until": 0})
            self.db.set_session("s1", state="active")
            self.assertFalse(self.wait()["wake"])

    def test_cancelled_wait_does_not_wake(self):
        with mock.patch.object(Heartbeat, "wait_for", return_value="cancelled"):
            self.assertFalse(self.wait()["wake"])

    def test_activity_event_via_api_cancels_waiter(self):
        self.db.set_settings({"heartbeat_minutes": 1})
        out = []
        t = threading.Thread(target=lambda: out.append(self.wait()))
        t.start()
        time.sleep(0.3)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/v1/events", body=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}),
                  headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        c.getresponse().read()
        t.join(5)
        self.assertEqual(out, [{"wake": False}])


class WakeClientTests(TmpHome):
    def run_client(self, payload, response):
        class In:
            buffer = type("B", (), {"read": staticmethod(lambda n: json.dumps(payload).encode())})

        class Conn:
            def __init__(self, *a, **k): pass
            def request(self, *a, **k): self.sent = True
            def getresponse(self): return mock.Mock(status=200, read=lambda: json.dumps(response).encode())
            def close(self): pass

        paths.token_path().write_text("t" * 40, encoding="utf-8")
        err = mock.Mock()
        with mock.patch.object(sys, "stdin", In), mock.patch.object(sys, "stderr", err), \
                mock.patch("http.client.HTTPConnection", Conn):
            return wakeclient.main(), err

    def test_exit_2_with_message_when_daemon_says_wake(self):
        paths.enabled_flag().touch()
        code, err = self.run_client({"background_tasks": [{"id": "b1"}]}, {"wake": True, "message": "controlla"})
        self.assertEqual(code, 2)
        err.write.assert_called_with("controlla")

    def test_exit_0_when_no_wake_no_tasks_or_switch_off(self):
        self.assertEqual(self.run_client({"background_tasks": [{"id": "b"}]}, {"wake": True, "message": "x"})[0], 0)
        paths.enabled_flag().touch()
        self.assertEqual(self.run_client({"background_tasks": []}, {"wake": False})[0], 0)
        self.assertEqual(self.run_client({"background_tasks": [{"id": "b"}]}, {"wake": False})[0], 0)

    def test_the_daemon_alone_decides_even_without_background_tasks(self):
        paths.enabled_flag().touch()  # a reply may arrive for an idle session that runs nothing in background
        code, err = self.run_client({"background_tasks": []}, {"wake": True, "message": "da email"})
        self.assertEqual(code, 2)
        err.write.assert_called_with("da email")


if __name__ == "__main__":
    unittest.main()


class ReplyDeliveryTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.token = auth.load_or_create_token()
        self.srv = Server(("127.0.0.1", 0), self.db, auth.Auth(self.token), Worker(self.db), lambda: None)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.enable(0)
        self.db.set_settings({"inbound_enabled": True, "inbound_to_claude": True, "heartbeat_mode": "off"})
        self.db.touch_session("s1", "C:/w/demo", time.time())
        self.db.set_session("s1", state="idle")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, payload=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request("POST", "/v1/heartbeat/wait", body=json.dumps(payload or {"session_id": "s1"}),
                  headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        r = c.getresponse()
        try:
            return json.loads(r.read())
        finally:
            c.close()

    def test_pending_reply_is_delivered_immediately_at_stop(self):
        self.db.add_inbound_message("s1", "c:/w/demo", "Procedi con A.", time.time())
        r = self.call()
        self.assertTrue(r["wake"])
        self.assertEqual(r["kind"], "reply")
        self.assertIn("Procedi con A.", r["message"])
        self.assertIn("messaggio dell'utente", r["message"].lower())
        self.assertEqual(self.db.pending_inbound("s1", time.time()), [])   # delivered once only
        self.assertEqual(self.db.one("SELECT state FROM sessions")["state"], "active")

    def test_reply_arriving_while_parked_wakes_the_hook(self):
        out = []
        t = threading.Thread(target=lambda: out.append(self.call()))
        t.start()
        time.sleep(0.4)
        self.db.add_inbound_message("s1", "c:/w/demo", "Nuova istruzione", time.time())
        self.srv.heartbeat.message_arrived("s1")
        t.join(8)
        self.assertTrue(out and out[0]["wake"])
        self.assertIn("Nuova istruzione", out[0]["message"])

    def test_nothing_is_parked_when_the_feature_is_off(self):
        self.db.set_settings({"inbound_to_claude": False})
        self.db.add_inbound_message("s1", "c:/w/demo", "x", time.time())
        t0 = time.time()
        self.assertFalse(self.call()["wake"])
        self.assertLess(time.time() - t0, 2)  # answered at once, the hook process exits

    def test_heartbeat_outcomes(self):
        from ccmail.heartbeat import Heartbeat
        hb = Heartbeat()
        threading.Timer(0.1, hb.message_arrived, ["s"]).start()
        self.assertEqual(hb.wait_for("s", 5, listen=True), "message")
        threading.Timer(0.1, hb.message_arrived, ["s"]).start()
        self.assertEqual(hb.wait_for("s", 0.3, listen=False), "elapsed")  # not listening: ignored
        threading.Timer(0.1, hb.note_activity, ["s"]).start()
        self.assertEqual(hb.wait_for("s", 5, listen=True), "cancelled")


class ReconnectTests(TmpHome):
    def test_hook_reparks_after_the_daemon_restarts(self):
        paths.token_path().write_text("t" * 40, encoding="utf-8")
        paths.enabled_flag().touch()
        calls = {"n": 0}

        class Conn:
            def __init__(self, *a, **k): pass
            def request(self, *a, **k):
                calls["n"] += 1
                if calls["n"] < 3:                # the daemon is restarting: connection refused twice
                    raise ConnectionRefusedError()
            def getresponse(self):
                return mock.Mock(status=200, read=lambda: json.dumps({"wake": True, "message": "ciao"}).encode())
            def close(self): pass

        class In:
            buffer = type("B", (), {"read": staticmethod(lambda n: b'{"session_id": "s1"}')})

        err = mock.Mock()
        with mock.patch.object(sys, "stdin", In), mock.patch.object(sys, "stderr", err), \
                mock.patch("http.client.HTTPConnection", Conn), mock.patch("time.sleep") as slept:
            code = wakeclient.main()
        self.assertEqual(code, 2)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(slept.call_count, 2)
        err.write.assert_called_with("ciao")

    def test_notification_does_not_cancel_a_parked_hook(self):
        from ccmail import policy
        self.assertNotIn("Notification", policy.ACTIVITY_EVENTS)
        for ev in ("UserPromptSubmit", "PostToolBatch", "SessionEnd"):
            self.assertIn(ev, policy.ACTIVITY_EVENTS)
