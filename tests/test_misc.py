import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

from ccmail import credstore, hookclient, installer, mailer, paths, render
from helpers import TmpHome


class InstallerTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.f = Path(self._tmp.name) / "settings.json"

    def test_merge_preserves_existing_and_is_idempotent(self):
        self.f.write_text(json.dumps({"theme": "x", "hooks": {"Stop": [
            {"matcher": "", "hooks": [{"type": "command", "command": "python other.py"}]}]}}), encoding="utf-8")
        installer.install(self.f)
        installer.install(self.f)
        d = json.loads(self.f.read_text(encoding="utf-8"))
        self.assertEqual(d["theme"], "x")
        cmds = [h["command"] for g in d["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(sum("-m ccmail hook" in c for c in cmds), 1)
        self.assertIn("python other.py", cmds)
        self.assertTrue(installer.is_installed(self.f))
        self.assertTrue(list(self.f.parent.glob("settings.json.bak-*")))

    def test_wake_and_bash_hooks_installed(self):
        installer.install(self.f)
        d = json.loads(self.f.read_text(encoding="utf-8"))
        wake = [h for g in d["hooks"]["Stop"] for h in g["hooks"] if "-m ccmail wake" in h["command"]]
        self.assertEqual(len(wake), 1)
        self.assertTrue(wake[0]["async"] and wake[0]["asyncRewake"])
        self.assertGreaterEqual(wake[0]["timeout"], 240 * 60)
        post = d["hooks"]["PostToolUse"][0]
        self.assertEqual(post["matcher"], "Bash")
        self.assertTrue(post["hooks"][0]["async"])

    def test_old_v01_install_is_reported_as_incomplete(self):
        self.f.write_text(json.dumps({"hooks": {ev: [{"matcher": "", "hooks": [
            {"type": "command", "command": f'"{sys.executable}" -m ccmail hook'}]}]
            for ev in ("Stop", "Notification")}}), encoding="utf-8")
        self.assertFalse(installer.is_installed(self.f))
        installer.install(self.f)
        self.assertTrue(installer.is_installed(self.f))

    def test_uninstall_leaves_others(self):
        self.f.write_text(json.dumps({"hooks": {"Stop": [
            {"matcher": "", "hooks": [{"type": "command", "command": "python other.py"}]}]}}), encoding="utf-8")
        installer.install(self.f)
        self.assertGreater(installer.uninstall(self.f), 0)
        d = json.loads(self.f.read_text(encoding="utf-8"))
        self.assertEqual(list(d["hooks"]), ["Stop"])
        self.assertFalse(installer.is_installed(self.f))

    def test_legacy_removal(self):
        legacy = {"hooks": {"Stop": [{"matcher": "", "hooks": [
            {"type": "command", "command": 'python "C:/u/.claude/hooks/email_notify.py"'}]}]}}
        self.f.write_text("\ufeff" + json.dumps(legacy), encoding="utf-8")  # BOM tolerated
        self.assertTrue(installer.has_legacy(self.f))
        installer.uninstall(self.f, legacy=True)
        self.assertFalse(installer.has_legacy(self.f))


class TraceTests(TmpHome):
    def test_trace_install_is_separate_from_main_hooks(self):
        f = Path(self._tmp.name) / "settings.json"
        installer.install(f)
        installer.trace_install(f)
        self.assertEqual(installer.uninstall(f), len(installer.EVENTS) + 3)  # +PostToolUse +PostToolBatch +wake; trace stays
        d = json.loads(f.read_text(encoding="utf-8"))
        self.assertTrue(all(h["async"] for g in d["hooks"]["PostToolUse"] for h in g["hooks"]))
        self.assertEqual(installer.trace_uninstall(f), len(installer.TRACE_EVENTS))
        self.assertNotIn("hooks", json.loads(f.read_text(encoding="utf-8")))

    def test_tracer_redacts_and_summarizes(self):
        from ccmail import tracer
        rec = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_input": {"command": "x password=hunter2", "run_in_background": True},
               "tool_response": {"stdout": "", "backgroundTaskId": "bg_1"}}
        class In:
            buffer = type("B", (), {"read": staticmethod(lambda n: json.dumps(rec).encode())})
        with mock.patch.object(sys, "stdin", In):
            self.assertEqual(tracer.main(), 0)
        raw = tracer.trace_file().read_text(encoding="utf-8")
        self.assertNotIn("hunter2", raw)
        line = tracer.summarize()[0]
        self.assertIn("run_in_background=True", line)
        self.assertIn("backgroundTaskId", line)


class HookClientTests(TmpHome):
    def run_hook(self, stdin: bytes, post_ok=False):
        class In:
            buffer = type("B", (), {"read": staticmethod(lambda n: stdin)})
        with mock.patch.object(sys, "stdin", In), mock.patch.object(hookclient, "_post", return_value=post_ok), \
                mock.patch.object(hookclient, "start_daemon"), mock.patch("time.sleep"):
            return hookclient.main()

    def test_switch_off_does_nothing(self):
        self.assertEqual(self.run_hook(b'{"a":1}'), 0)
        self.assertEqual(list(paths.spool_dir().glob("*.json")), [])

    def test_daemon_down_spools_and_exits_zero(self):
        paths.enabled_flag().touch()
        self.assertEqual(self.run_hook(b'{"hook_event_name":"Stop"}'), 0)
        self.assertEqual(len(list(paths.spool_dir().glob("*.json"))), 1)

    def test_garbage_stdin_never_raises(self):
        paths.enabled_flag().touch()
        self.assertEqual(self.run_hook(b"not json"), 0)


class RenderMailTests(unittest.TestCase):
    def test_subject_has_no_newlines(self):
        s = {"content_mode": "full", "max_body_chars": 500}
        subj, _ = render.build("Stop", {"last_assistant_message": "riga1\r\nBcc: x@y.z"}, s, "pro\nj", 100)
        self.assertNotIn("\n", subj)
        self.assertNotIn("\r", subj)

    def test_message_headers_safe(self):
        m = mailer.build_message("a@b.c", "d@e.f", "ciao\r\nBcc: z@z.z", "body")
        self.assertIsNone(m["Bcc"])


@unittest.skipUnless(os.name == "nt", "Windows only")
class CredStoreTests(TmpHome):
    def test_roundtrip_credential_manager(self):
        target = f"ccmail:test-{uuid.uuid4().hex[:8]}"
        try:
            self.assertEqual(credstore.save("u@example.com", "pässwörd 123", target), credstore.BACKEND_CREDMAN)
            self.assertEqual(credstore.load(target), ("u@example.com", "pässwörd 123", credstore.BACKEND_CREDMAN))
        finally:
            credstore.delete(target)
        self.assertIsNone(credstore._credman_read(target))

    def test_test_homes_never_share_the_real_target(self):
        self.assertNotEqual(credstore.default_target(), credstore.TARGET)
        old = os.environ.pop("CCMAIL_HOME")
        try:
            self.assertEqual(credstore.default_target(), credstore.TARGET)
        finally:
            os.environ["CCMAIL_HOME"] = old

    def test_dpapi_roundtrip(self):
        blob = credstore._dpapi("segreto".encode(), True)
        self.assertNotIn(b"segreto", blob)
        self.assertEqual(credstore._dpapi(blob, False), b"segreto")


if __name__ == "__main__":
    unittest.main()
