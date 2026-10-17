"""1.0.2 polish found by a first-time user: readable queue, honest backup message, `open` without a browser,
a daemon hint that is true, and a PATH message that does not contradict itself."""
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from ccmail import cli, installer, launcher
from helpers import TmpHome


class QueueTextTests(unittest.TestCase):
    def test_counts_become_words(self):
        self.assertEqual(cli.queue_text({"failed": 1, "sent": 3}, True), "1 failed, 3 sent")
        self.assertEqual(cli.queue_text({"failed": 1, "sent": 3}, False), "1 fallite, 3 inviate")

    def test_empty_and_unknown_states(self):
        self.assertEqual(cli.queue_text({}, True), "empty")
        self.assertEqual(cli.queue_text({}, False), "vuota")
        self.assertEqual(cli.queue_text({"weird": 2}, True), "2 weird")  # never hides a state it does not know


class CliPolishTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.claude = Path(self._tmp.name) / "claude"
        self._old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)
        self._old_lang = os.environ.get("CCMAIL_LANG")
        os.environ["CCMAIL_LANG"] = "en"

    def tearDown(self):
        for key, old in (("CLAUDE_CONFIG_DIR", self._old_cfg), ("CCMAIL_LANG", self._old_lang)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        super().tearDown()

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_install_returns_no_backup_for_a_new_file_and_the_backup_for_an_existing_one(self):
        f = Path(self._tmp.name) / "settings.json"
        self.assertIsNone(installer.install(f))
        bak = installer.install(f)
        self.assertTrue(bak.is_file() and bak.name.startswith("settings.json.bak-"))

    def test_install_hooks_message_does_not_claim_a_backup_that_does_not_exist(self):
        with mock.patch.object(cli, "_ensure_cli"):
            _code, first = self.run_cli("install-hooks")
            self.assertIn("new file", first)
            self.assertNotIn("backup:", first)
            _code, second = self.run_cli("install-hooks")
        self.assertIn("backup: settings.json.bak-", second)

    def test_open_prints_the_one_time_link_when_no_browser_can_be_opened(self):
        info = Path(self._tmp.name) / "daemon.json"
        with mock.patch.object(cli, "_ensure_daemon", return_value=True), \
                mock.patch.object(cli, "_api", return_value=(200, {"nonce": "N0NCE"})), \
                mock.patch.object(cli.paths, "info_path", return_value=info), \
                mock.patch.object(cli.webbrowser, "open", return_value=False):
            info.write_text(json.dumps({"port": 47999}), encoding="utf-8")
            code, out = self.run_cli("open")
        self.assertEqual(code, 0)
        self.assertIn("http://127.0.0.1:47999/auth?n=N0NCE", out)

    def test_open_does_not_print_the_one_time_link_when_the_browser_opened(self):
        info = Path(self._tmp.name) / "daemon.json"
        with mock.patch.object(cli, "_ensure_daemon", return_value=True), \
                mock.patch.object(cli, "_api", return_value=(200, {"nonce": "N0NCE"})), \
                mock.patch.object(cli.paths, "info_path", return_value=info), \
                mock.patch.object(cli.webbrowser, "open", return_value=True):
            info.write_text(json.dumps({"port": 47999}), encoding="utf-8")
            code, out = self.run_cli("open")
        self.assertEqual(code, 0)
        self.assertIn("http://127.0.0.1:47999/", out)
        self.assertNotIn("N0NCE", out)

    def test_doctor_does_not_tell_to_run_on_to_start_the_daemon(self):
        with mock.patch.object(cli, "_api", return_value=None):
            checks = cli._doctor_checks(False)
        hints = [h for level, title, _d, h in checks if title == "Daemon"]
        self.assertEqual(len(hints), 1)
        self.assertNotIn("mail-notify on", hints[0])
        self.assertIn("mail-notify open", hints[0])

    def test_path_message_is_one_sentence_not_a_contradiction(self):
        tgt = Path(self._tmp.name) / "bin" / "mail-notify.cmd"
        with mock.patch.object(launcher, "target", lambda *a, **k: tgt), \
                mock.patch.object(launcher, "install", return_value=(tgt, False)):
            _code, out = self.run_cli("install-hooks")
        self.assertIn("is not on PATH", out)
        self.assertNotIn("ready", out)  # «ready» + «not on PATH» in the same line was contradictory
        self.assertIn("python -m ccmail", out)


if __name__ == "__main__":
    unittest.main()
