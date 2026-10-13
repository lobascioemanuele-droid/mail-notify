"""The short `mail-notify` command: where the launcher goes, what it runs, and that it never clobbers a foreign file."""
import os
import tempfile
import unittest
from pathlib import Path

from ccmail import launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def test_target_per_system(self):
        w = launcher.target(True, {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}, self.home)
        self.assertEqual(w.name, "mail-notify.cmd")
        self.assertEqual(w.parent.name, "WindowsApps")
        p = launcher.target(False, {}, self.home)
        self.assertEqual(p, self.home / ".local" / "bin" / "mail-notify")

    def test_content_runs_the_module_and_names_the_program(self):
        c = launcher.content(r"C:\Python\python.exe", True)
        self.assertIn('"C:\Python\python.exe" -m ccmail %*', c)
        self.assertIn("CCMAIL_PROG=mail-notify", c)
        s = launcher.content("/usr/bin/python3", False)
        self.assertTrue(s.startswith("#!/bin/sh"))
        self.assertIn('exec "/usr/bin/python3" -m ccmail "$@"', s)

    def test_install_and_uninstall_posix(self):
        env = {"PATH": str(self.home / ".local" / "bin")}
        path, found = launcher.install("/usr/bin/python3", False, env, self.home)
        self.assertTrue(path.is_file() and found)
        if os.name != "nt":
            self.assertTrue(os.access(path, os.X_OK))
        self.assertTrue(launcher.is_ours(path))
        self.assertEqual(launcher.uninstall(False, env, self.home), path)
        self.assertFalse(path.exists())

    def test_install_reports_when_folder_is_not_on_path(self):
        _, found = launcher.install("/usr/bin/python3", False, {"PATH": "/usr/bin"}, self.home)
        self.assertFalse(found)

    def test_never_overwrites_or_removes_a_foreign_file(self):
        path = launcher.target(False, {}, self.home)
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            launcher.install("/usr/bin/python3", False, {}, self.home)
        self.assertIsNone(launcher.uninstall(False, {}, self.home))
        self.assertTrue(path.exists())

    def test_reinstall_replaces_our_own_launcher(self):
        launcher.install("/old/python", False, {}, self.home)
        path, _ = launcher.install("/new/python", False, {}, self.home)
        self.assertIn("/new/python", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
