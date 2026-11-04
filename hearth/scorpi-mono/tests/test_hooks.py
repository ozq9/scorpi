import os
import tempfile
import unittest

from tests import mock_llm
from tests.support import FakeCtx, call


class HooksTest(unittest.TestCase):
    def setUp(self):
        from scorpi.config import SETTINGS
        self.dir = tempfile.mkdtemp(prefix="hooks-")
        SETTINGS.update({"hooks": []})

    def tearDown(self):
        from scorpi.config import SETTINGS
        SETTINGS.update({"hooks": []})

    def test_after_edit_formatter_and_blocking_failure(self):
        from scorpi.config import SETTINGS
        SETTINGS.update({"hooks": [
            {"event": "after_edit", "match": "*.txt", "command": "tr a-z A-Z < {file} > {file}.tmp && mv {file}.tmp {file}", "name": "upper"},
            {"event": "after_edit", "match": "*.py", "command": "echo nope; exit 3", "block": True, "name": "lint"},
        ]})
        ctx = FakeCtx(self.dir)
        res = call("write_file", ctx, path="a.txt", content="hello\n")
        self.assertTrue(res.ok, res.text)
        with open(os.path.join(self.dir, "a.txt")) as fh:
            self.assertEqual(fh.read(), "HELLO\n")
        self.assertIn("Hook upper ok", res.text)
        # the formatter changed the file, yet the agent can edit it right away (no "changed on disk")
        res = call("edit_file", ctx, path="a.txt", find="HELLO", replace="bye")
        self.assertTrue(res.ok, res.text)
        res = call("write_file", ctx, path="b.py", content="x = 1\n")
        self.assertFalse(res.ok)
        self.assertIn("Hook lint failed (exit 3)", res.text)
        self.assertIn("nope", res.text)

    def test_before_command_can_veto(self):
        from scorpi.agent import Agent
        from scorpi.config import SETTINGS
        SETTINGS.update({"hooks": [{"event": "before_command", "match": r"npm publish", "command": "echo 'publishing is not allowed'; exit 1", "block": True}]})
        agent = Agent(None, model="x", workdir=self.dir)
        self.assertIn("publishing is not allowed", agent._command_hooks("run_shell", {"command": "npm publish"}))
        self.assertEqual(agent._command_hooks("run_shell", {"command": "npm test"}), "")

    def test_after_turn_sends_the_agent_back(self):
        from scorpi.agent import Agent
        from scorpi.config import SETTINGS
        srv, url = mock_llm.start()
        try:
            SETTINGS.update({"key_groq": "t", "cloud_urls": {"groq": url}, "cloud_anon": False,
                             "hooks": [{"event": "after_turn", "command": "test -f done.txt || (echo 'done.txt is missing'; exit 1)", "block": True, "name": "check"}]})
            mock_llm.REQUESTS.clear()
            mock_llm.SCRIPT[:] = [
                {"content": "All done."},
                {"tool_calls": [{"name": "write_file", "arguments": {"path": "done.txt", "content": "ok\n"}}]},
                {"content": "Now really done."},
            ]
            agent = Agent(None, model="@groq/llama-3.3-70b", groups=["files"], workdir=self.dir)
            text = agent.run_turn([{"role": "user", "content": "finish"}], "finish")
            self.assertIn("Now really done.", text)
            self.assertTrue(os.path.isfile(os.path.join(self.dir, "done.txt")))
            self.assertIn("done.txt is missing", str(mock_llm.REQUESTS[1]["payload"]["messages"]))
        finally:
            srv.shutdown()

    def test_clean_drops_bad_hooks(self):
        from scorpi import hooks
        out = hooks.clean([{"event": "nope", "command": "x"}, {"event": "after_turn", "command": ""}, "junk",
                           {"event": "after_turn", "command": "npm test", "timeout": 99999}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["timeout"], 1800)


if __name__ == "__main__":
    unittest.main()
