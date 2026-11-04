import os
import tempfile
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401  (sets up the data folder)


class LedgerTest(unittest.TestCase):
    def ledger(self):
        from scorpi.proof import Ledger
        return Ledger("/proj")

    def test_tests_after_the_last_change_prove_it(self):
        led = self.ledger()
        led.tool("write_file", {"path": "app.py"}, True, "Wrote app.py\nChecks passed (app.py)")
        led.tool("run_tests", {"command": "pytest"}, True, "===== 4 passed in 0.1s =====")
        card = led.card(has_tests=True)
        self.assertEqual(card["verdict"], "verified")
        self.assertIn("Tests pass: 4 passed in 0.1s", card["items"][0]["text"])
        self.assertEqual(card["files"], ["app.py"])

    def test_tests_before_the_last_change_do_not(self):
        led = self.ledger()
        led.tool("write_file", {"path": "app.py"}, True, "Checks passed")
        led.tool("run_tests", {"command": "pytest"}, True, "4 passed")
        led.tool("edit_file", {"path": "app.py"}, True, "Checks passed")
        card = led.card(has_tests=True)
        self.assertEqual(card["verdict"], "partial")
        self.assertIn("The tests ran before the last change, not after it", card["unverified"])

    def test_failures_and_open_problems(self):
        led = self.ledger()
        led.tool("write_file", {"path": "app.py"}, True, "PROBLEMS FOUND — fix them now:\n- app.py line 3")
        led.tool("run_tests", {"command": "pytest"}, False, "FAILED (failures=2)")
        card = led.card(has_tests=True)
        self.assertEqual(card["verdict"], "failing")
        self.assertTrue(any("Syntax problems left in app.py" == i["text"] for i in card["items"]))

    def test_web_pages_need_a_look(self):
        led = self.ledger()
        led.tool("write_file", {"path": "index.html"}, True, "Checks passed")
        card = led.card(has_tests=False)
        self.assertIn("The page was not opened in a browser after the last change", card["unverified"])
        self.assertIn("This project has no tests", card["unverified"])
        led.tool("visual_check", {}, True, "No errors", {"after": "/api/visual/x/a.png", "errors": [], "failed": []})
        card = led.card(has_tests=False)
        self.assertTrue(any(i["kind"] == "page" and i["ok"] and i["image"] for i in card["items"]))

    def test_docs_only_and_nothing_changed(self):
        led = self.ledger()
        self.assertIsNone(led.card())
        led.tool("write_file", {"path": "README.md"}, True, "Wrote README.md")
        card = led.card(has_tests=True)
        self.assertEqual(card["unverified"], [])  # documentation needs no test run

    def test_summary_line(self):
        from scorpi.proof import summary
        led = self.ledger()
        led.tool("write_file", {"path": "app.py"}, True, "Checks passed")
        line = summary(led.card(has_tests=True))
        self.assertTrue(line.startswith("Partly verified:"), line)
        self.assertIn("not checked: no tests ran after the last change", line.lower())


class AgentProofTest(unittest.TestCase):
    def test_a_coding_run_ends_with_proof(self):
        from scorpi.agent import Agent
        from scorpi.config import SETTINGS
        srv, url = mock_llm.start()
        saved = {k: SETTINGS.get(k) for k in ("key_groq", "cloud_urls", "cloud_anon", "model", "code_model", "tool_policy")}
        SETTINGS.update({"key_groq": "test", "cloud_urls": {"groq": url}, "cloud_anon": False, "model": "@groq/qwen3-coder-480b",
                         "code_model": "", "tool_policy": {"write_file": "allow", "run_tests": "allow"}})
        try:
            folder = tempfile.mkdtemp(prefix="proof-")
            mock_llm.SCRIPT[:] = [
                {"tool_calls": [{"name": "write_file", "arguments": {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}}]},
                {"tool_calls": [{"name": "write_file", "arguments": {"path": "tests/test_calc.py", "content":
                    "import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"}}]},
                {"tool_calls": [{"name": "run_tests", "arguments": {}}]},
                {"content": "Done."},
            ]
            events = []
            agent = Agent(None, model="@groq/qwen3-coder-480b", skill="code", groups=["code", "files"], emit=events.append,
                          persona={"name": "Coder", "instructions": "", "workdir": folder, "project": "proof-calc"}, max_steps=6)
            agent.run_turn([{"role": "user", "content": "add()"}], "add()")
            self.assertEqual(agent.proof["verdict"], "verified", agent.proof)
            self.assertEqual(agent.parts[-1]["type"], "card")
            self.assertTrue(any(e.get("type") == "card" and e["display"]["type"] == "proof" for e in events))
        finally:
            srv.shutdown()
            SETTINGS.update(saved)


if __name__ == "__main__":
    unittest.main()
