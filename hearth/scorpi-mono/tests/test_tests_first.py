import os
import tempfile
import time
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401  (sets up the data folder)

TEST = ("import unittest\nfrom shop import total\n\nclass T(unittest.TestCase):\n    def test_total(self):\n"
        "        self.assertEqual(total([2, 3]), 5)\n\n    def test_empty(self):\n        self.assertEqual(total([]), 0)\n")
GOOD = "def total(prices):\n    return sum(prices)\n"
COMMAND = "python3 -m unittest discover -s tests"


class ContractTest(unittest.TestCase):
    def test_test_files(self):
        from scorpi.contract import is_test_file
        for path in ("tests/test_a.py", "test_a.py", "a_test.go", "src/app.test.ts", "web/__tests__/x.js", "spec/models/user_spec.rb",
                     "src/UserTest.java", "conftest.py"):
            self.assertTrue(is_test_file(path), path)
        for path in ("app.py", "src/testing_utils_helper.py.bak", "latest.py", "contest.py"):
            self.assertFalse(is_test_file(path), path)

    def test_lock_puts_changes_back(self):
        from scorpi.contract import Contract
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "tests"))
        with open(os.path.join(root, "tests", "test_shop.py"), "w") as fh:
            fh.write(TEST)
        deal = Contract(["tests/test_shop.py"], COMMAND, "", root)
        self.assertTrue(deal.locked(os.path.join(root, "tests", "test_shop.py")))
        with open(os.path.join(root, "tests", "test_shop.py"), "w") as fh:
            fh.write("# emptied\n")
        self.assertEqual(deal.tampered(), ["tests/test_shop.py"])
        with open(os.path.join(root, "tests", "test_shop.py")) as fh:
            self.assertEqual(fh.read(), TEST)
        self.assertEqual(deal.tampered(), [])


class TestsFirstJobTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        cls.saved = {k: SETTINGS.get(k) for k in ("key_groq", "cloud_urls", "cloud_anon", "model", "code_model", "tool_policy", "checkpoints")}
        SETTINGS.update({"key_groq": "test", "cloud_urls": {"groq": cls.url}, "cloud_anon": False, "model": "@groq/qwen3-coder-480b",
                         "code_model": "", "checkpoints": False,
                         "tool_policy": {"run_tests": "allow", "write_file": "allow", "edit_file": "allow", "run_shell": "allow"}})

    @classmethod
    def tearDownClass(cls):
        from scorpi.config import SETTINGS
        cls.srv.shutdown()
        SETTINGS.update(cls.saved)

    def _approve(self, tool, timeout=15):
        from scorpi import events
        deadline = time.time() + timeout
        while time.time() < deadline:
            for aid, a in list(events.APPROVALS.items()):
                if a["tool"] == tool:
                    events.resolve_approval(aid, "allow")
                    return a
            time.sleep(0.05)
        self.fail("no %s approval came" % tool)

    def test_tests_then_build_against_them(self):
        from scorpi import agents
        agents.init()
        mock_llm.REQUESTS.clear()
        mock_llm.SCRIPT[:] = [
            # tests first: implementation is refused, tests are written, run (fail) and submitted
            {"tool_calls": [{"name": "write_file", "arguments": {"path": "shop.py", "content": GOOD}}]},
            {"tool_calls": [{"name": "write_file", "arguments": {"path": "tests/test_shop.py", "content": TEST}}]},
            {"tool_calls": [{"name": "run_tests", "arguments": {"command": COMMAND}}]},
            {"tool_calls": [{"name": "submit_tests", "arguments": {"files": ["tests/test_shop.py"], "command": COMMAND,
                                                                  "summary": "Checks total() of a list and of an empty list."}}]},
            {"content": "Tests approved."},
            # the build: an attempt to loosen the test is refused, a shell trick is undone, then the real fix
            {"tool_calls": [{"name": "write_file", "arguments": {"path": "tests/test_shop.py", "content": "# nothing to test\n"}}]},
            {"tool_calls": [{"name": "run_shell", "arguments": {"command": "echo '# gone' > tests/test_shop.py"}}]},
            {"content": "Done (but it isn't)."},
            {"tool_calls": [{"name": "write_file", "arguments": {"path": "shop.py", "content": GOOD}}]},
            {"content": "Now total() works and the approved tests pass."},
        ]
        job = agents.assign("coder", "Add total(prices) to shop.py", title="Shop total", project="shop-tdd", tests=True)
        self.assertTrue(job.public()["testing"])
        approval = self._approve("submit_tests")
        self.assertEqual(approval["summary"]["kind"], "tests")
        self.assertIn("### tests/test_shop.py", approval["summary"]["plan"])
        deadline = time.time() + 30
        while (job.status == "running" or job.run_note != "Tests approved: building until they pass") and time.time() < deadline:
            if job.status != "running" and job.run_note:
                break
            time.sleep(0.05)
        while job.status == "running" and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(job.status, "done", job.error)
        with open(os.path.join(job.workdir, "tests", "test_shop.py")) as fh:
            self.assertEqual(fh.read(), TEST)  # the approved test is exactly as approved
        with open(os.path.join(job.workdir, "shop.py")) as fh:
            self.assertEqual(fh.read(), GOOD)
        sent = [m.get("content") or "" for r in mock_llm.REQUESTS for m in r["payload"]["messages"]]
        self.assertTrue(any("only test files may change" in t for t in sent))
        self.assertTrue(any("one of the tests the user approved" in t for t in sent))
        self.assertTrue(any("they were put back" in t for t in sent))
        self.assertTrue(any("Not done yet: the approved tests still fail" in t for t in sent))
        offered = {t["function"]["name"] for t in mock_llm.REQUESTS[0]["payload"]["tools"]}
        self.assertIn("submit_tests", offered)
        build = {t["function"]["name"] for t in mock_llm.REQUESTS[6]["payload"]["tools"]}
        self.assertIn("propose_test_change", build)
        self.assertNotIn("submit_tests", build)


if __name__ == "__main__":
    unittest.main()
