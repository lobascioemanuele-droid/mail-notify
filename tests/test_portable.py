"""Portability: per-OS paths, credential backends, autostart files, providers, language, skill and doctor."""
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from ccmail import autostart, cli, config, credstore, i18n, installer, paths, providers, render, skillinstall, suggest
from helpers import TmpHome


class DataDirTests(unittest.TestCase):
    def test_per_os_locations(self):
        home = Path("/home/u")
        self.assertEqual(paths.default_data_dir("linux", {}, home), home / ".config" / "ccmail")
        self.assertEqual(paths.default_data_dir("linux", {"XDG_CONFIG_HOME": "/x"}, home), Path("/x") / "ccmail")
        self.assertEqual(paths.default_data_dir("darwin", {}, home),
                         home / "Library" / "Application Support" / "ccmail")
        self.assertEqual(paths.default_data_dir("windows", {"APPDATA": "C:/R"}, home), Path("C:/R") / "ccmail")
        self.assertEqual(paths.default_data_dir("windows", {}, home), home / "AppData" / "Roaming" / "ccmail")

    def test_claude_dir_override(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/custom/claude"}):
            self.assertEqual(paths.claude_dir(), Path("/custom/claude"))
            self.assertEqual(installer.default_settings_path(), Path("/custom/claude") / "settings.json")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(paths.claude_dir(), Path.home() / ".claude")


class CredstoreFallbackTests(TmpHome):
    def test_plain_file_fallback_when_no_secure_store(self):
        """No Keychain / Secret Service: the file fallback is used, labelled as not encrypted, and round-trips."""
        with mock.patch.object(credstore, "_credman_write", side_effect=credstore.CredError("none")), \
                mock.patch.object(credstore, "_credman_read", return_value=None), \
                mock.patch.object(credstore, "_windows", return_value=False):
            backend = credstore.save("u@example.com", "pw-1234", "t")
            self.assertEqual(backend, credstore.BACKEND_PLAIN)
            self.assertFalse(credstore.is_secure(backend))
            self.assertEqual(credstore.load("t"), ("u@example.com", "pw-1234", credstore.BACKEND_PLAIN))

    def test_secure_backends_are_flagged(self):
        for b in (credstore.BACKEND_CREDMAN, credstore.BACKEND_KEYCHAIN, credstore.BACKEND_SECRET):
            self.assertTrue(credstore.is_secure(b))
        self.assertFalse(credstore.is_secure(credstore.BACKEND_FILE))
        self.assertFalse(credstore.is_secure(None))


class AutostartTests(unittest.TestCase):
    ARGV = ["/opt/py thon/bin/python", "-m", "ccmail", "daemon"]

    def test_launchd_plist(self):
        x = autostart.launchd_plist(self.ARGV)
        self.assertIn("<string>com.ccmail.daemon</string>", x)
        self.assertIn("<string>/opt/py thon/bin/python</string>", x)
        self.assertIn("<key>RunAtLoad</key>", x)

    def test_systemd_unit_quotes_spaces(self):
        x = autostart.systemd_unit(self.ARGV)
        self.assertIn('ExecStart="/opt/py thon/bin/python" -m ccmail daemon', x)
        self.assertIn("WantedBy=default.target", x)

    def test_desktop_entry(self):
        self.assertIn("Exec=", autostart.desktop_entry(self.ARGV))

    def test_xml_escaping(self):
        self.assertIn("a&amp;b", autostart.launchd_plist(["a&b"]))


class ProviderTests(unittest.TestCase):
    def test_guess(self):
        self.assertEqual(providers.guess("a@gmail.com"), "gmail")
        self.assertEqual(providers.guess("a@outlook.com"), "outlook")
        self.assertEqual(providers.guess("a@icloud.com"), "icloud")
        self.assertEqual(providers.guess("a@yahoo.it"), "yahoo")
        self.assertEqual(providers.guess("a@azienda.it"), "custom")
        self.assertEqual(providers.guess("nonsense"), "custom")

    def test_presets_pass_config_validation(self):
        for key in providers.ORDER:
            pv = providers.PROVIDERS[key]
            if key == "custom":
                continue
            out = config.validate({"smtp_host": pv["smtp_host"], "smtp_port": pv["smtp_port"],
                                   "smtp_security": pv["smtp_security"], "imap_host": pv["imap_host"]})
            self.assertEqual(out["smtp_host"], pv["smtp_host"])

    def test_new_settings_validate(self):
        self.assertEqual(config.validate({"language": "en"}), {"language": "en"})
        self.assertEqual(config.validate({"device_name": " Laptop "}), {"device_name": "Laptop"})
        with self.assertRaises(ValueError):
            config.validate({"language": "fr"})


class LanguageTests(TmpHome):
    def test_forced_and_setting(self):
        self.assertEqual(i18n.resolve({"language": "en"}), "en")
        self.assertEqual(i18n.resolve({"language": "it"}), "it")
        with mock.patch.dict(os.environ, {"CCMAIL_LANG": "en"}):
            self.assertEqual(i18n.resolve({"language": "auto"}), "en")

    def _build(self, lang):
        s = self.db.get_settings()
        s.update(language=lang, inbound_enabled=True)
        return render.build("Stop", {"session_id": "abcdef123456",
                                     "last_assistant_message": "Done. Tests pass: 12 test su 12."},
                            s, "proj", 300, chat_title="My chat", ref="ccm-0123456789ab", inbound=True)

    def test_english_mail_has_no_italian_labels(self):
        subject, body = self._build("en")
        for w in ("Progetto", "Esito", "Comandi suggeriti", "Durata turno", "Rispondi"):
            self.assertNotIn(w, subject + body)
        for w in ("Project: proj", "OUTCOME", "SUGGESTED COMMANDS", "Turn duration", "Reply to this e-mail"):
            self.assertIn(w, body)

    def test_italian_mail_unchanged(self):
        subject, body = self._build("it")
        self.assertIn("Progetto: proj", body)
        self.assertIn("ESITO", body)
        self.assertIn("Rif: ccm-0123456789ab", body)

    def test_device_name_line(self):
        s = self.db.get_settings()
        s["device_name"] = "Portatile"
        self.assertIn("Dispositivo: Portatile", render.header("p", s, "s1"))
        s["language"] = "en"
        self.assertIn("Device: Portatile", render.header("p", s, "s1"))

    def test_english_suggestions(self):
        out = suggest.suggest("Stop", None, "Shall I go ahead with the plan?", lang="en")
        self.assertTrue(any(x.startswith("Yes, go ahead") for x in out))

    def test_english_reply_words_are_commands(self):
        from ccmail import inbound
        self.assertEqual(inbound.parse_command("PAUSE 30"), ("pause", 30))
        self.assertEqual(inbound.parse_command("off"), ("off", None))


class SkillInstallTests(TmpHome):
    def setUp(self):
        super().setUp()
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(Path(self._tmp.name) / "claude")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        super().tearDown()

    def test_install_status_uninstall(self):
        self.assertFalse(skillinstall.state()["installed"])
        r = skillinstall.install()
        self.assertTrue(Path(r["path"]).exists())
        st = skillinstall.state()
        self.assertTrue(st["installed"] and st["ours"] and st["current"])
        self.assertTrue(skillinstall.uninstall())
        self.assertFalse(skillinstall.state()["installed"])

    def test_foreign_skill_is_backed_up_not_lost(self):
        t = skillinstall.target_path()
        t.parent.mkdir(parents=True)
        t.write_text("my own skill", encoding="utf-8")
        r = skillinstall.install()
        self.assertIsNotNone(r["backup"])
        self.assertEqual(Path(r["backup"]).read_text(encoding="utf-8"), "my own skill")

    def test_uninstall_leaves_foreign_skill(self):
        t = skillinstall.target_path()
        t.parent.mkdir(parents=True)
        t.write_text("my own skill", encoding="utf-8")
        self.assertFalse(skillinstall.uninstall())
        self.assertTrue(t.exists())

    def test_packaged_skill_is_generic(self):
        text = skillinstall.packaged_path().read_text(encoding="utf-8")
        self.assertIn("name: mail-notify", text)
        for personal in ("Lo" + "bas", "Oro" + "pan", "Eman" + "uele", "itis" + "andria", "C:\\Users"):
            self.assertNotIn(personal, text)


class HookInterpreterTests(TmpHome):
    def test_interpreters_listed_and_doctor_flags_missing(self):
        f = Path(self._tmp.name) / "settings.json"
        installer.install(f)
        exes = installer.hook_interpreters(f)
        self.assertEqual(exes, [sys.executable])
        data = json.loads(f.read_text(encoding="utf-8"))
        data["hooks"]["Stop"][0]["hooks"][0]["command"] = '"/no/such/python" -m ccmail hook'
        f.write_text(json.dumps(data), encoding="utf-8")
        self.assertIn("/no/such/python", installer.hook_interpreters(f))


class DoctorTests(TmpHome):
    def test_doctor_runs_and_leaks_nothing(self):
        env = {"CLAUDE_SETTINGS": str(Path(self._tmp.name) / "settings.json"),
               "CLAUDE_CONFIG_DIR": str(Path(self._tmp.name) / "claude")}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stdout(buf), \
                mock.patch.object(credstore, "load", return_value=("me@example.com", "TOP-SECRET-PW", "x")):
            code = cli.main(["doctor"])
        out = buf.getvalue()
        self.assertNotIn("TOP-SECRET-PW", out)
        self.assertIn("[FAIL] Hook non installati", out)
        self.assertEqual(code, 1)


class DaemonPortFallbackTests(TmpHome):
    def test_busy_port_taken_by_other_program_falls_back(self):
        import socket
        import threading
        import time
        from ccmail import daemon
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy = blocker.getsockname()[1]
        t = threading.Thread(target=daemon.run, kwargs={"foreground": False, "port": busy}, daemon=True)
        t.start()
        try:
            info = paths.info_path()
            for _ in range(60):
                if info.exists():
                    break
                time.sleep(0.1)
            self.assertTrue(info.exists(), "daemon did not start on a fallback port")
            port = json.loads(info.read_text(encoding="utf-8"))["port"]
            self.assertNotEqual(port, busy)
            from ccmail import auth
            import http.client
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            c.request("GET", "/v1/status", headers={"Authorization": "Bearer " + auth.read_token()})
            self.assertEqual(c.getresponse().status, 200)
            c.close()
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                c.request("POST", "/v1/shutdown", body="{}",
                          headers={"Authorization": "Bearer " + auth.read_token(), "Content-Type": "application/json"})
                c.getresponse().read()
            except OSError:
                pass
        finally:
            blocker.close()
            t.join(timeout=5)
            import logging
            for h in list(logging.getLogger("ccmail").handlers):
                logging.getLogger("ccmail").removeHandler(h)
                h.close()


if __name__ == "__main__":
    unittest.main()
