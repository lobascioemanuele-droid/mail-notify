"""A Stop event without `last_assistant_message` (turn ended on a plan, a question or tool calls) still mails what
Claude wrote, read from the transcript."""
import json
import unittest
from pathlib import Path

from ccmail import policy, transcript
from helpers import TmpHome

T0 = 1_800_000_000.0


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_result():
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}


def assistant(*blocks, sidechain=False):
    o = {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}
    if sidechain:
        o["isSidechain"] = True
    return o


def text(t):
    return {"type": "text", "text": t}


def tool(name, **inp):
    return {"type": "tool_use", "name": name, "input": inp}


class TranscriptTests(TmpHome):
    def write(self, *entries, raw_tail=""):
        p = Path(self._tmp.name) / "chat.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in entries) + "\n" + raw_tail, encoding="utf-8")
        return str(p)

    def test_plan_to_approve_is_the_message(self):
        p = self.write(user("fai un piano"), assistant(text("Ecco il piano")),
                       assistant(tool("ExitPlanMode", plan="1. Prima cosa\n2. Seconda cosa")))
        self.assertEqual(transcript.last_turn_text(p), "1. Prima cosa\n2. Seconda cosa")

    def test_questions_with_their_options(self):
        p = self.write(user("parti"), assistant(tool("AskUserQuestion", questions=[
            {"question": "Quale database?", "options": [{"label": "SQLite", "description": "locale"}, {"label": "Postgres"}]}])))
        self.assertEqual(transcript.last_turn_text(p), "Quale database?\n- SQLite: locale\n- Postgres")

    def test_desktop_app_wrap_up_card_is_the_message(self):
        # the Claude desktop app closes a turn with this tool: recap + the question for you + suggested replies
        p = self.write(user("lavora"), assistant(text("Sto lavorando")), assistant(tool(
            "mcp__ccd_turn__wrap_up", recap="Ho finito la prima parte.", ask="Procedo con la seconda?",
            actions=[{"label": "Sì", "message": "Procedi pure con la seconda parte"}, {"label": "No", "message": ""}],
            blocked=True)), tool_result())
        self.assertEqual(transcript.last_turn_text(p),
                         "Ho finito la prima parte.\n\nProcedo con la seconda?\n\n- Sì: Procedi pure con la seconda parte")

    def test_wrap_up_with_only_a_recap(self):
        p = self.write(user("x"), assistant(tool("mcp__ccd_turn__wrap_up", recap="Fatto.", ask="", actions=[], blocked=False)))
        self.assertEqual(transcript.last_turn_text(p), "Fatto.")

    def test_falls_back_to_the_last_text_of_the_turn_when_it_ends_on_tool_calls(self):
        p = self.write(user("lavora"), assistant(text("Sto controllando i file")), assistant(tool("Read", file_path="x")),
                       tool_result(), assistant(tool("Bash", command="ls")))
        self.assertEqual(transcript.last_turn_text(p), "Sto controllando i file")

    def test_never_reaches_back_into_a_previous_turn(self):
        p = self.write(user("primo"), assistant(text("Risposta del turno precedente")), user("secondo"),
                       assistant(tool("Bash", command="ls")))
        self.assertEqual(transcript.last_turn_text(p), "")

    def test_ignores_subagent_messages(self):
        p = self.write(user("lavora"), assistant(text("del sotto-agente"), sidechain=True))
        self.assertEqual(transcript.last_turn_text(p), "")

    def test_missing_unreadable_or_partial_files_give_nothing(self):
        self.assertEqual(transcript.last_turn_text(None), "")
        self.assertEqual(transcript.last_turn_text(str(Path(self._tmp.name) / "nope.jsonl")), "")
        p = self.write(user("x"), assistant(text("ok")), raw_tail='{"type": "assistant", "mess')  # half-written line
        self.assertEqual(transcript.last_turn_text(p), "ok")

    def test_stop_without_a_message_mails_the_plan_and_fills_the_full_message_box(self):
        self.enable(T0 - 9999)
        self.db.set_settings({"html_mail": True})  # the scrollable box belongs to the HTML alternative
        plan = "Piano:\n" + "\n".join(f"{i}. passo numero {i} del lavoro da fare" for i in range(1, 15))
        p = self.write(user("fai un piano"), assistant(tool("ExitPlanMode", plan=plan)))
        ev = {"session_id": "s1", "cwd": "C:/w/progetto", "transcript_path": p}
        policy.handle_event(self.db, {**ev, "hook_event_name": "UserPromptSubmit"}, now=T0)
        self.assertEqual(policy.handle_event(self.db, {**ev, "hook_event_name": "Stop"}, now=T0 + 400), "queued")
        row = self.db.q("SELECT body, full_text FROM outbox")[0]
        self.assertIn("passo numero", row["full_text"])
        self.assertIn("ESITO", row["body"])

    def test_stop_with_a_message_never_touches_the_transcript(self):
        self.enable(T0 - 9999)
        p = self.write(user("x"), assistant(tool("ExitPlanMode", plan="IL PIANO")))
        ev = {"session_id": "s2", "cwd": "C:/w/progetto", "transcript_path": p}
        policy.handle_event(self.db, {**ev, "hook_event_name": "UserPromptSubmit"}, now=T0)
        policy.handle_event(self.db, {**ev, "hook_event_name": "Stop", "last_assistant_message": "Fatto."}, now=T0 + 400)
        row = self.db.q("SELECT body, full_text FROM outbox")[0]
        self.assertNotIn("IL PIANO", row["body"] + (row["full_text"] or ""))


if __name__ == "__main__":
    unittest.main()
