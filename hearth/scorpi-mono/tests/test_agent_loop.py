import os
import tempfile
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401  (sets up the data folder)


class AgentLoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        SETTINGS.update({"key_groq": "test", "cloud_urls": {"groq": cls.url}, "cloud_anon": False, "model": "@groq/qwen3-coder-480b",
                         "code_model": "", "tool_policy": {"run_tests": "allow", "run_shell": "allow"}})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_write_edit_test_in_project_folder(self):
        from scorpi.agent import Agent
        folder = tempfile.mkdtemp(prefix="job-")
        mock_llm.REQUESTS.clear()
        mock_llm.SCRIPT[:] = [
            {"tool_calls": [{"name": "write_file", "arguments": {"path": "calc.py", "content": "def add(a, b):\n    return a - b\n"}}]},
            {"tool_calls": [{"name": "write_file", "arguments": {"path": "tests/test_calc.py", "content":
                "import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"}}]},
            {"tool_calls": [{"name": "run_tests", "arguments": {}}]},
            {"tool_calls": [{"name": "edit_file", "arguments": {"path": "calc.py", "find": "return a - b", "replace": "return a + b"}}]},
            {"tool_calls": [{"name": "run_tests", "arguments": {}}]},
            {"content": "Done: add() works and the tests pass."},
        ]
        agent = Agent(None, model="@groq/qwen3-coder-480b", skill="code", groups=["code", "files"],
                      persona={"name": "Coder", "instructions": "", "workdir": folder, "project": "calc"}, max_steps=10)
        text = agent.run_turn([{"role": "user", "content": "build a calculator with tests"}], "build a calculator with tests")
        self.assertIn("tests pass", text)
        self.assertTrue(os.path.isfile(os.path.join(folder, "calc.py")))
        tools = [p for p in agent.parts if p["type"] == "tool"]
        self.assertEqual([t["name"] for t in tools], ["write_file", "write_file", "run_tests", "edit_file", "run_tests"])
        self.assertFalse(tools[2]["ok"])  # the first test run fails (a - b)
        self.assertTrue(tools[4]["ok"], tools[4]["result"])
        # the model saw the failing test output in the tool message
        sent = mock_llm.REQUESTS[3]["payload"]["messages"]
        self.assertTrue(any(m.get("role") == "tool" and "FAILED" in (m.get("content") or "") for m in sent))
        # new tools are offered to the model
        offered = {t["function"]["name"] for t in mock_llm.REQUESTS[0]["payload"]["tools"]}
        self.assertTrue({"grep", "glob", "repo_map", "apply_patch", "run_tests", "edit_file"} <= offered, offered)


if __name__ == "__main__":
    unittest.main()
