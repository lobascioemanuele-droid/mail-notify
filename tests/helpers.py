import os
import tempfile
import unittest
from unittest import mock

from ccmail.db import DB, set_enabled
from ccmail import chats, paths


class TmpHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CCMAIL_HOME")
        self._old_sessions = os.environ.get("CCMAIL_CLAUDE_SESSIONS")
        os.environ["CCMAIL_HOME"] = self._tmp.name
        # never read the real desktop app metadata from tests: point at an (initially absent) fake folder
        self.sessions_dir = os.path.join(self._tmp.name, "app-sessions")
        os.environ["CCMAIL_CLAUDE_SESSIONS"] = self.sessions_dir
        chats.reset_cache()
        # never depend on the developer's real ~/.claude.json (Claude account name)
        self._name_patch = mock.patch("ccmail.identity.claude_display_name", return_value="")
        self._name_patch.start()
        self.db = DB(paths.db_path())

    def tearDown(self):
        self._name_patch.stop()
        self.db.close()
        for name, old in (("CCMAIL_HOME", self._old), ("CCMAIL_CLAUDE_SESSIONS", self._old_sessions)):
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
        self._tmp.cleanup()

    def add_chat(self, chat_id="local_aaaa", cli="cli-1", title="Chat di prova", root="C:/w/progetto",
                 archived=False, last_ms=1_800_000_000_000, account="acc", org="org"):
        """Write a fake desktop-app chat metadata file (same shape as the app's) and rescan."""
        import json
        d = os.path.join(self.sessions_dir, account, org)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{chat_id}.json"), "w", encoding="utf-8") as f:
            json.dump({"sessionId": chat_id, "cliSessionId": cli, "title": title, "originCwd": root, "cwd": root,
                       "isArchived": archived, "lastActivityAt": last_ms}, f)
        chats.scan(self.db, force=True)

    def enable(self, now=0):
        set_enabled(self.db, True, now=now)
