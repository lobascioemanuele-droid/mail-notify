"""cli._api: only a 2xx answer means "our daemon is there"; a foreign daemon (401) must read as not running."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from ccmail import auth, cli, paths
from helpers import TmpHome


def serve(status):
    class H(BaseHTTPRequestHandler):
        def _answer(self):
            body = json.dumps({"ok": status == 200}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _answer

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class ApiTests(TmpHome):
    def point_at(self, srv):
        auth.load_or_create_token()
        paths.info_path().write_text(json.dumps({"pid": 1, "port": srv.server_address[1]}), encoding="utf-8")

    def test_foreign_daemon_answering_401_is_not_ours(self):
        srv = serve(401)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.point_at(srv)
        self.assertIsNone(cli._api("GET", "/v1/status"))
        self.assertIsNone(cli._api("POST", "/v1/shutdown", {}))

    def test_our_daemon_answering_200(self):
        srv = serve(200)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.point_at(srv)
        st, data = cli._api("GET", "/v1/status")
        self.assertEqual((st, data), (200, {"ok": True}))

    def test_nothing_listening(self):
        srv = serve(200)
        port = srv.server_address[1]
        srv.shutdown()
        srv.server_close()
        auth.load_or_create_token()
        paths.info_path().write_text(json.dumps({"pid": 1, "port": port}), encoding="utf-8")
        self.assertIsNone(cli._api("GET", "/v1/status"))


if __name__ == "__main__":
    unittest.main()
