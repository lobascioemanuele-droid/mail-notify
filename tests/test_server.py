import http.client
import json
import threading
import unittest

from ccmail import auth
from ccmail.server import Server
from ccmail.worker import Worker
from helpers import TmpHome


class ServerTests(TmpHome):
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

    def req(self, method, path, body=None, headers=None, host=None):
        h = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json", **(headers or {})}
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest(method, path, skip_host=True)
        for k, v in h.items():
            c.putheader(k, v)
        data = json.dumps(body).encode() if body is not None else b""
        c.putheader("Content-Length", str(len(data)))
        c.endheaders(data)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, dict(r.getheaders()), raw

    def bearer(self):
        return {"Authorization": f"Bearer {self.token}"}

    def web_session(self):
        _, d = (lambda r: (r[0], json.loads(r[2])))(self.req("POST", "/v1/open-nonce", {}, self.bearer()))
        st, hdr, _ = self.req("GET", f"/auth?n={d['nonce']}")
        self.assertEqual(st, 302)
        cookie = hdr["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", hdr["Set-Cookie"])
        self.assertIn("SameSite=Strict", hdr["Set-Cookie"])
        _, _, raw = self.req("GET", "/v1/csrf", headers={"Cookie": cookie})
        return cookie, json.loads(raw)["csrf"]

    def test_rebinding_host_rejected(self):
        self.assertEqual(self.req("GET", "/v1/status", headers=self.bearer(), host="evil.example:80")[0], 403)

    def test_foreign_origin_rejected(self):
        h = {**self.bearer(), "Origin": "http://evil.example"}
        self.assertEqual(self.req("GET", "/v1/status", headers=h)[0], 403)

    def test_api_requires_auth(self):
        self.assertEqual(self.req("GET", "/v1/status")[0], 401)
        self.assertEqual(self.req("POST", "/v1/events", {}, {"Authorization": "Bearer nope"})[0], 401)

    def test_static_page_and_headers(self):
        st, hdr, raw = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"ccmail", raw)
        self.assertIn("default-src 'none'", hdr["Content-Security-Policy"])

    def test_event_ingest_with_bearer(self):
        st, _, raw = self.req("POST", "/v1/events", {"hook_event_name": "UserPromptSubmit", "session_id": "a"}, self.bearer())
        self.assertEqual((st, json.loads(raw)["decision"]), (200, "recorded"))

    def test_nonce_is_single_use(self):
        n = json.loads(self.req("POST", "/v1/open-nonce", {}, self.bearer())[2])["nonce"]
        self.assertEqual(self.req("GET", f"/auth?n={n}")[0], 302)
        self.assertEqual(self.req("GET", f"/auth?n={n}")[0], 403)

    def test_cookie_write_needs_csrf(self):
        cookie, csrf = self.web_session()
        body = {"cooldown_s": 90}
        self.assertEqual(self.req("PUT", "/v1/settings", body, {"Cookie": cookie})[0], 401)
        self.assertEqual(self.req("PUT", "/v1/settings", body, {"Cookie": cookie, "X-CSRF": csrf})[0], 200)
        self.assertEqual(self.db.get_settings()["cooldown_s"], 90)

    def test_settings_validation(self):
        h = self.bearer()
        for bad in ({"smtp_port": 0}, {"recipient": "a@b.c\r\nBcc: x@y.z"}, {"enabled": True}, {"nope": 1},
                    {"notify_types": ["bogus"]}):
            self.assertEqual(self.req("PUT", "/v1/settings", bad, h)[0], 400, bad)

    def test_watchdog_heartbeat_combination_is_rejected(self):
        put = lambda body: self.req("PUT", "/v1/settings", body, self.bearer())
        st, _, raw = put({"watchdog_minutes": 15, "heartbeat_minutes": 30})
        self.assertEqual(st, 400)
        self.assertIn("watchdog_minutes", json.loads(raw)["error"])
        self.assertIn("60", json.loads(raw)["error"])  # tells the minimum
        self.assertEqual(put({"watchdog_minutes": 60, "heartbeat_minutes": 30})[0], 200)
        self.assertEqual(put({"watchdog_minutes": 0})[0], 200)
        self.assertEqual(put({"watchdog_minutes": 20, "heartbeat_mode": "off"})[0], 200)
        # changing only the heartbeat so that the stored watchdog would become invalid is refused too
        self.assertEqual(put({"heartbeat_mode": "hook", "heartbeat_minutes": 30})[0], 400)
        self.assertEqual(self.db.get_settings()["heartbeat_mode"], "off")

    def test_legacy_invalid_combo_shows_a_warning(self):
        self.db.set_settings({"watchdog_minutes": 15, "heartbeat_minutes": 30})
        ov = self.get_json("/v1/overview")
        self.assertTrue(any("Watchdog in pausa" in w for w in ov["warnings"]))

    def test_toggle_sends_no_mail(self):
        self.req("POST", "/v1/toggle", {"enabled": True}, self.bearer())
        self.req("POST", "/v1/toggle", {"enabled": False}, self.bearer())
        self.assertEqual(self.db.q("SELECT * FROM outbox"), [])

    def test_shutdown_is_bearer_only(self):
        cookie, csrf = self.web_session()
        self.assertEqual(self.req("POST", "/v1/shutdown", {}, {"Cookie": cookie, "X-CSRF": csrf})[0], 403)

    def get_json(self, path):
        st, _, raw = self.req("GET", path, headers=self.bearer())
        self.assertEqual(st, 200, path)
        return json.loads(raw)

    def test_overview_sessions_events_filter(self):
        self.req("POST", "/v1/toggle", {"enabled": True}, self.bearer())
        for p in ({"hook_event_name": "UserPromptSubmit", "session_id": "a", "cwd": "C:/w/demo"},
                  {"hook_event_name": "Stop", "session_id": "a", "cwd": "C:/w/demo", "last_assistant_message": "x"}):
            self.req("POST", "/v1/events", p, self.bearer())
        ov = self.get_json("/v1/overview")
        self.assertTrue(ov["enabled"])
        self.assertEqual(ov["hourly"]["cap"], 10)
        self.assertGreaterEqual(ov["last_24h"]["skipped"], 1)
        self.assertEqual(ov["last_24h"]["skipped_by_reason"], {"skip:short_turn": 1})
        sess = self.get_json("/v1/sessions")
        self.assertEqual(sess[0]["project"], "demo")
        self.assertEqual([e["event"] for e in self.get_json("/v1/events?event=Stop")], ["Stop"])
        self.assertEqual(len(self.get_json("/v1/events?decision=skip")), 1)
        self.assertEqual(self.req("GET", "/v1/events?limit=abc", headers=self.bearer())[0], 400)

    def test_settings_shape(self):
        d = self.get_json("/v1/settings")
        self.assertEqual(set(d), {"values", "defaults", "options", "language_resolved"})
        self.assertNotIn("snooze_until", d["values"])
        self.assertIn("permission_prompt", d["options"]["notification_types"])

    def test_simulate_and_snooze(self):
        self.req("POST", "/v1/toggle", {"enabled": True}, self.bearer())
        self.db.set_settings({"mute_after_toggle_s": 0})
        sim = lambda **k: json.loads(self.req("POST", "/v1/simulate", {"event": "Stop", **k}, self.bearer())[2])
        self.assertEqual(sim(elapsed_s=5)["reason"], "short_turn")
        ok = sim(elapsed_s=600, message="Fatto")
        self.assertTrue(ok["would_send"])
        self.assertIn("Fatto", ok["subject"])
        self.assertEqual(self.db.q("SELECT * FROM events"), [])  # dry run writes nothing
        self.req("POST", "/v1/snooze", {"minutes": 30}, self.bearer())
        self.assertEqual(sim(elapsed_s=600)["reason"], "snoozed")
        self.req("POST", "/v1/snooze", {"minutes": 0}, self.bearer())
        self.assertTrue(sim(elapsed_s=600)["would_send"])
        self.assertEqual(self.req("POST", "/v1/snooze", {"minutes": -1}, self.bearer())[0], 400)
        self.assertEqual(self.req("POST", "/v1/simulate", {"event": "Nope"}, self.bearer())[0], 400)

    def test_keepalive_survives_unread_bodies(self):
        # regression: POSTs whose handler ignores the body used to desync a persistent connection
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {**self.bearer(), "Content-Type": "application/json"}
        for path in ("/v1/history/clear", "/v1/outbox/1/retry", "/v1/history/clear"):
            c.request("POST", path, body="{}", headers=h)
            r = c.getresponse()
            r.read()
            self.assertEqual(r.status, 200, path)
        c.request("GET", "/v1/overview", headers=self.bearer())
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 200)
        c.close()

    def test_static_flat_names_only(self):
        self.assertEqual(self.req("GET", "/app.js")[0], 200)
        for bad in ("/../paths.py", "/..%2fpaths.py", "/web/app.js", "/nope.js", "/__init__.py"):
            self.assertEqual(self.req("GET", bad)[0], 404 if bad != "/" else 200, bad)

    def test_content_type_enforced(self):
        h = {**self.bearer(), "Content-Type": "text/plain"}
        self.assertEqual(self.req("POST", "/v1/events", {"a": 1}, h)[0], 400)


if __name__ == "__main__":
    unittest.main()
