"""`mail-notify setup`: hooks + skill + short command in one go, never the password."""
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from ccmail import cli, launcher
from helpers import TmpHome


class SetupTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.claude = Path(self._tmp.name) / "claude"
        self.bin = Path(self._tmp.name) / "bin" / "mail-notify.cmd"
        self._old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)
        self._p = mock.patch.object(launcher, "target", lambda *a, **k: self.bin)
        self._p.start()
        # the output depends on whether `mail-notify` is on this machine's PATH: make it deterministic
        self._w = mock.patch.object(cli.shutil, "which", side_effect=lambda n: "/usr/bin/mail-notify")
        self._w.start()

    def tearDown(self):
        self._w.stop()
        self._p.stop()
        if self._old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._old_cfg
        super().tearDown()

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_setup_installs_hooks_skill_and_command(self):
        code, out = self.run_cli("setup")
        self.assertEqual(code, 0)
        settings = json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("Stop", settings["hooks"])
        self.assertTrue((self.claude / "skills" / "mail-notify" / "SKILL.md").is_file())
        self.assertTrue(self.bin.is_file() and launcher.is_ours(self.bin))
        self.assertIn("mail-notify init", out)  # no credentials in this fresh home: the user is told what is next
        self.assertIn("Riavvia Claude Code", out)

    def test_setup_is_idempotent_and_keeps_the_launcher(self):
        self.run_cli("setup")
        first = self.bin.read_text(encoding="utf-8")
        code, _out = self.run_cli("setup")
        self.assertEqual(code, 0)
        self.assertEqual(self.bin.read_text(encoding="utf-8"), first)
        settings = json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))
        stop_cmds = [h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(len(stop_cmds), len(set(stop_cmds)))  # no duplicated hook entries

    def test_next_steps_use_python_dash_m_when_the_short_command_is_not_on_path(self):
        which = lambda name: {"python": "C:/Python/python.exe"}.get(name)  # noqa: E731  no mail-notify/ccmail
        with mock.patch.object(cli.shutil, "which", side_effect=which):
            self.assertEqual(cli.runnable_name(), "python -m ccmail")
            code, out = self.run_cli("setup")
        self.assertEqual(code, 0)
        self.assertIn("python -m ccmail init", out)
        self.assertIn("non è ancora riconosciuto", out)

    def test_next_steps_use_the_short_command_when_it_is_on_path(self):
        with mock.patch.object(cli.shutil, "which", side_effect=lambda n: "C:/bin/mail-notify.cmd"):
            self.assertEqual(cli.runnable_name(), "mail-notify")
            code, out = self.run_cli("setup")
        self.assertIn("«mail-notify init»", out)
        self.assertNotIn("non è ancora riconosciuto", out)

    def test_falls_back_to_the_interpreter_path_when_nothing_is_on_path(self):
        with mock.patch.object(cli.shutil, "which", return_value=None):
            self.assertTrue(cli.runnable_name().endswith("-m ccmail"))
            self.assertIn("python", cli.runnable_name().lower())

    def test_no_skill_leaves_the_skill_to_the_plugin(self):
        code, out = self.run_cli("setup", "--no-skill")
        self.assertEqual(code, 0)
        self.assertFalse((self.claude / "skills" / "mail-notify" / "SKILL.md").exists())
        self.assertTrue((self.claude / "settings.json").is_file())  # hooks are still installed

    def test_setup_never_asks_for_or_touches_the_password(self):
        with mock.patch("builtins.input", side_effect=AssertionError("setup must not prompt")), \
                mock.patch("getpass.getpass", side_effect=AssertionError("setup must not prompt")):
            code, _out = self.run_cli("setup")
        self.assertEqual(code, 0)

    def test_autostart_only_when_asked(self):
        with mock.patch.object(cli.installer, "autostart", return_value=True) as a:
            self.run_cli("setup")
            a.assert_not_called()
            self.run_cli("setup", "--autostart")
            a.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
