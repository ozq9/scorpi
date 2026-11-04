import os
import tempfile
import time
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401  (sets up the data folder)


class LessonsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        cls.saved = {k: SETTINGS.get(k) for k in ("key_groq", "cloud_urls", "cloud_anon", "model", "code_model")}
        SETTINGS.update({"key_groq": "test", "cloud_urls": {"groq": cls.url}, "cloud_anon": False, "model": "@groq/qwen3-coder-480b", "code_model": ""})

    @classmethod
    def tearDownClass(cls):
        from scorpi.config import SETTINGS
        cls.srv.shutdown()
        SETTINGS.update(cls.saved)

    def test_corrections(self):
        from scorpi.lessons import looks_like_correction
        for text in ("Don't use jQuery, we use plain JS.", "Always run the linter before you finish", "Use pnpm instead of npm",
                     "no more inline styles please", "You should never commit .env files"):
            self.assertTrue(looks_like_correction(text), text)
        for text in ("Add a contact page", "What does this function do?", "ok"):
            self.assertFalse(looks_like_correction(text), text)

    def test_suggest_accept_and_dedupe(self):
        from scorpi import lessons
        folder = tempfile.mkdtemp(prefix="lessons-")
        with open(os.path.join(folder, "AGENTS.md"), "w") as fh:
            fh.write("# AGENTS.md\n\n## Commands\n- Run `npm test`.\n")
        mock_llm.SCRIPT[:] = [{"content": "Use plain JavaScript, not jQuery."}]
        item = lessons.consider("lessons-proj", folder, "Don't use jQuery, we use plain JS.", "a message to the agent", "c1", wait=True)
        self.assertEqual(item["rule"], "Use plain JavaScript, not jQuery.")
        self.assertEqual([i["id"] for i in lessons.pending("lessons-proj")], [item["id"]])
        path = lessons.accept(item["id"], folder)
        with open(path) as fh:
            text = fh.read()
        self.assertIn("## Commands\n- Run `npm test`.", text)
        self.assertIn(lessons.SECTION + "\n- Use plain JavaScript, not jQuery.\n", text)
        self.assertEqual(lessons.pending("lessons-proj"), [])
        # the same rule again is not suggested twice
        mock_llm.SCRIPT[:] = [{"content": "Use plain JavaScript, not jQuery."}]
        self.assertIsNone(lessons.consider("lessons-proj", folder, "stop using jquery!", "a message to the agent", wait=True))
        # a second rule goes into the same section
        mock_llm.SCRIPT[:] = [{"content": "Store data in SQLite, not JSON files."}]
        other = lessons.consider("lessons-proj", folder, "Never store data in JSON files, use SQLite", "asking for changes to a plan", wait=True)
        lessons.accept(other["id"], folder, "Store all data in SQLite.")
        with open(path) as fh:
            self.assertIn("- Use plain JavaScript, not jQuery.\n- Store all data in SQLite.\n", fh.read())

    def test_one_off_corrections_are_not_rules(self):
        from scorpi import lessons
        mock_llm.SCRIPT[:] = [{"content": "NONE"}]
        self.assertIsNone(lessons.consider("lessons-proj", tempfile.mkdtemp(), "No, make the button blue instead", "a message to the agent", wait=True))

    def test_rejected_plan_note_suggests_a_rule(self):
        from scorpi import events, lessons
        from scorpi.agent import Agent
        folder = tempfile.mkdtemp(prefix="lessons-")

        class Run:
            id, conv_id, origin, cancelled, inbox = "r1", "conv-l", "agent", False, []

            def emit(self, e):
                pass
        mock_llm.SCRIPT[:] = [
            {"tool_calls": [{"name": "submit_plan", "arguments": {"plan": "1. Build it with jQuery and a JSON file for storage."}}]},
            {"content": "Store data in SQLite, not JSON files."},  # the rule (written in the background)
            {"content": "Revised."},
        ]
        agent = Agent(Run(), model="@groq/qwen3-coder-480b", skill="code", groups=["code", "files"], plan_mode=True,
                      persona={"name": "Coder", "instructions": "", "workdir": folder, "project": "lessons-plan"}, max_steps=1)

        def answer():
            deadline = time.time() + 10
            while time.time() < deadline:
                for aid, a in list(events.APPROVALS.items()):
                    if a["tool"] == "submit_plan":
                        events.resolve_approval(aid, "deny", "Never use JSON files for storage; we always use SQLite.")
                        return
                time.sleep(0.05)
        import threading
        threading.Thread(target=answer, daemon=True).start()
        agent.run_turn([{"role": "user", "content": "plan a todo app"}], "plan a todo app")
        deadline = time.time() + 10
        while not lessons.pending("lessons-plan") and time.time() < deadline:
            time.sleep(0.05)
        found = lessons.pending("lessons-plan")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source"], "asking for changes to a plan")
        lessons.dismiss(found[0]["id"])
        self.assertEqual(lessons.pending("lessons-plan"), [])


if __name__ == "__main__":
    unittest.main()
