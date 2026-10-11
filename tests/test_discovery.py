import json
import os
import unittest
from pathlib import Path
from unittest import mock

from ccmail import chats, policy, projroot
from helpers import TmpHome

T0 = 1_800_000_000.0
ROOT = "C:\\Users\\alice\\acme - Copy"
TDIR = "C--Users-alice-acme---copy"


def hook(name, sid, cwd=ROOT, tpath=None, **kw):
    tpath = tpath or f"C:/Users/alice/.claude/projects/{TDIR}/{sid}.jsonl"
    return {"hook_event_name": name, "session_id": sid, "cwd": cwd, "transcript_path": tpath, **kw}


class MultiRootTests(TmpHome):
    def make_root(self, name, chat_id, cli, title):
        d = Path(self._tmp.name, name, "acc", "org")
        d.mkdir(parents=True)
        (d / f"{chat_id}.json").write_text(json.dumps({
            "sessionId": chat_id, "cliSessionId": cli, "title": title, "originCwd": ROOT, "cwd": ROOT,
            "isArchived": False, "lastActivityAt": 1_800_000_000_000}), encoding="utf-8")
        return str(Path(self._tmp.name, name))

    def test_roots_are_merged_and_the_missing_one_is_ignored(self):
        a = self.make_root("pkg", "local_1", "cli-1", "Chat uno")
        b = self.make_root("classic", "local_2", "cli-2", "Chat due")
        missing = str(Path(self._tmp.name, "does-not-exist"))
        with mock.patch.dict(os.environ, {"CCMAIL_CLAUDE_SESSIONS": os.pathsep.join([missing, a, b])}):
            chats.reset_cache()
            self.assertEqual(chats.scan(self.db, force=True), 2)
            rep = chats.root_report()
        self.assertEqual([r["exists"] for r in rep], [False, True, True])
        self.assertEqual(sorted(r["title"] for r in self.db.q("SELECT title FROM chats")), ["Chat due", "Chat uno"])

    def test_default_roots_include_the_packaged_app_profile(self):
        local = Path(self._tmp.name, "Local")
        prof = local / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude" / "claude-code-sessions"
        prof.mkdir(parents=True)
        env = {"LOCALAPPDATA": str(local), "APPDATA": str(Path(self._tmp.name, "Roaming"))}
        with mock.patch.dict(os.environ, env), mock.patch.object(chats, "_windows", return_value=True):
            os.environ.pop("CCMAIL_CLAUDE_SESSIONS", None)
            rs = chats.roots()
        self.assertEqual(rs[0], prof)
        self.assertTrue(str(rs[-1]).endswith(os.path.join("Roaming", "Claude", "claude-code-sessions")))


class TranscriptTitleTests(TmpHome):
    def write(self, lines, size_pad=0):
        p = Path(self._tmp.name, "t.jsonl")
        body = "\n".join(json.dumps(x) if isinstance(x, dict) else x for x in lines) + "\n"
        p.write_text(("x" * size_pad + "\n" if size_pad else "") + body, encoding="utf-8")
        return str(p)

    def test_latest_title_wins_and_only_the_tail_is_read(self):
        p = self.write([{"type": "custom-title", "customTitle": "Vecchio", "sessionId": "s"},
                        {"type": "user", "message": "ciao"},
                        {"type": "custom-title", "customTitle": "Nome  attuale", "sessionId": "s"},
                        {"type": "assistant", "message": "ok"}], size_pad=600_000)
        self.assertEqual(chats.transcript_title(p), "Nome attuale")

    def test_missing_or_titleless_transcript_is_empty(self):
        self.assertEqual(chats.transcript_title(str(Path(self._tmp.name, "nope.jsonl"))), "")
        self.assertEqual(chats.transcript_title(self.write([{"type": "user"}])), "")

    def test_cache_follows_file_changes(self):
        p = self.write([{"type": "custom-title", "customTitle": "A"}])
        self.assertEqual(chats.transcript_title(p), "A")
        Path(p).write_text(json.dumps({"type": "custom-title", "customTitle": "BB"}) + "\n", encoding="utf-8")
        self.assertEqual(chats.transcript_title(p), "BB")

    def test_outside_chat_uses_transcript_title_in_mail(self):
        p = self.write([{"type": "custom-title", "customTitle": "Sessione da terminale"}])
        self.enable(T0 - 9999)
        policy.handle_event(self.db, hook("UserPromptSubmit", "solo-cli", tpath=p), now=T0)
        policy.handle_event(self.db, hook("Stop", "solo-cli", tpath=p, last_assistant_message="ok"), now=T0 + 400)
        self.assertIn("Chat: Sessione da terminale", self.db.q("SELECT body FROM outbox")[0]["body"])


class ProjectResolutionTests(TmpHome):
    def test_subfolder_never_becomes_a_project(self):
        # exactly the real case: same transcript folder, Claude moved into a subfolder ("uscite")
        policy.handle_event(self.db, hook("UserPromptSubmit", "s1", cwd=ROOT), now=T0)
        policy.handle_event(self.db, hook("UserPromptSubmit", "s2", cwd=ROOT + "\\uscite"), now=T0 + 5)
        names = {r["project"] for r in self.db.q("SELECT project FROM sessions")}
        self.assertEqual(names, {"acme - Copy"})
        self.assertEqual([r["name"] for r in self.db.q("SELECT name FROM projects")], ["acme - Copy"])

    def test_subfolder_first_is_corrected_when_the_root_appears(self):
        policy.handle_event(self.db, hook("UserPromptSubmit", "s2", cwd=ROOT + "\\uscite"), now=T0)
        self.assertEqual(self.db.one("SELECT project FROM sessions")["project"], "uscite")  # best guess so far
        policy.handle_event(self.db, hook("UserPromptSubmit", "s1", cwd=ROOT), now=T0 + 5)
        self.assertEqual({r["project"] for r in self.db.q("SELECT project FROM sessions")}, {"acme - Copy"})

    def test_known_chat_decides_the_project_even_for_unlinked_sessions(self):
        self.add_chat("local_a", "cli-a", "Chat A", ROOT)
        policy.handle_event(self.db, hook("UserPromptSubmit", "orphan", cwd=ROOT + "\\uscite"), now=T0)
        s = self.db.one("SELECT * FROM sessions WHERE session_id='orphan'")
        self.assertEqual(s["project"], "acme - Copy")
        self.assertIsNone(s["chat_id"])

    def test_repair_realigns_old_rows_and_drops_ghost_projects(self):
        self.db.touch_session("old1", ROOT, T0, transcript_path=f"C:/x/.claude/projects/{TDIR}/old1.jsonl")
        self.db.touch_session("old2", ROOT + "\\uscite", T0, transcript_path=f"C:/x/.claude/projects/{TDIR}/old2.jsonl")
        self.db.x("UPDATE sessions SET project='uscite', project_dir='c:/users/alice/acme - copy/uscite' "
                  "WHERE session_id='old2'")
        self.db.x("INSERT INTO projects(project_dir, name, enabled) VALUES('c:/users/alice/acme - copy/uscite',"
                  "'uscite',1)")
        res = projroot.repair(self.db, now=T0)
        self.assertGreaterEqual(res["sessions_realigned"], 1)
        self.assertEqual({r["project"] for r in self.db.q("SELECT project FROM sessions")}, {"acme - Copy"})
        self.assertNotIn("uscite", [r["name"] for r in self.db.q("SELECT name FROM projects")])

    def test_repair_keeps_a_disabled_switch(self):
        self.db.touch_session("old1", ROOT, T0)
        projroot.repair(self.db, now=T0)
        self.db.set_project_enabled(ROOT, "acme - Copy", False, T0)
        projroot.repair(self.db, now=T0)
        self.assertFalse(self.db.project_enabled(ROOT))

    def test_encode_matches_claude_code_folder_naming(self):
        self.assertEqual(projroot.encode_dir(ROOT), TDIR.lower())  # compared case-insensitively


if __name__ == "__main__":
    unittest.main()
