import os
import tempfile
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401  (sets up the data folder)


class ScoreTest(unittest.TestCase):
    def setUp(self):
        from scorpi import scores
        scores.reset_state()

    tearDown = setUp

    def test_unknown_is_neutral_and_results_move_models(self):
        from scorpi import scores
        self.assertEqual(scores.score("@groq/a"), (0.5, 0))
        for _ in range(6):
            scores.record("@groq/bad", "test_fail")
            scores.record("@groq/good", "test_pass")
        scores.record("@groq/new", "test_pass")  # one result is not enough to move it
        good, n = scores.score("@groq/good")
        self.assertGreater(good, 0.7)
        self.assertEqual(n, 6)
        self.assertLess(scores.score("@groq/bad")[0], 0.3)
        self.assertEqual(scores.rank(["@groq/bad", "@groq/new", "@groq/other", "@groq/good"]),
                         ["@groq/good", "@groq/new", "@groq/other", "@groq/bad"])

    def test_tool_results(self):
        from scorpi import scores
        scores.tool_result("@x/m", "edit_file", False, "Edited a.py\nPROBLEMS FOUND — fix them now:\n- a.py: line 2")
        scores.tool_result("@x/m", "write_file", True, "Wrote a.py\nChecks passed (a.py)")
        scores.tool_result("@x/m", "run_tests", False, "FAILED (failures=1)")
        scores.tool_result("@x/m", "grep", False, "Error: bad arguments for grep: unexpected keyword")
        row = next(r for r in scores.board() if r["model"] == "@x/m")
        self.assertEqual(row["signals"]["checks"]["good"], 1)
        self.assertEqual(row["signals"]["checks"]["bad"], 1)
        self.assertEqual(row["signals"]["tests"]["bad"], 1)
        self.assertEqual(row["signals"]["tools"]["bad"], 1)


class BenchTasksTest(unittest.TestCase):
    """The benchmark's own tasks: the starting files fail the hidden tests and the reference solution passes them."""

    def test_tasks_are_sound(self):
        from scorpi import bench
        for task in bench.TASKS:
            folder = tempfile.mkdtemp(prefix="bench-check-")
            bench.prepare(folder, task)
            passed, _ = bench.verify(folder, task)
            self.assertFalse(passed, task["id"] + " passes without any work")
            for name, body in task["solution"].items():
                with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
                    fh.write(body)
            passed, output = bench.verify(folder, task)
            self.assertTrue(passed, task["id"] + ":\n" + output)


class BenchRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        cls.saved = {k: SETTINGS.get(k) for k in ("key_groq", "key_cerebras", "cloud_urls", "cloud_anon", "model", "code_model", "learn_models")}
        SETTINGS.update({"key_groq": "test", "key_cerebras": "test", "cloud_urls": {"groq": cls.url, "cerebras": cls.url}, "cloud_anon": False,
                         "model": "@groq/qwen3-coder-480b", "code_model": "auto", "learn_models": True})

    @classmethod
    def tearDownClass(cls):
        from scorpi import limits, scores
        from scorpi.config import SETTINGS
        cls.srv.shutdown()
        SETTINGS.update(cls.saved)
        scores.reset_state()
        limits.reset_state()

    def setUp(self):
        from scorpi import limits, scores
        scores.reset_state()
        limits.reset_state()

    def test_a_model_solves_a_task(self):
        from scorpi import bench
        task = next(t for t in bench.TASKS if t["id"] == "bugfix")
        mock_llm.SCRIPT[:] = [
            {"tool_calls": [{"name": "write_file", "arguments": {"path": "stats.py", "content": task["solution"]["stats.py"]}}]},
            {"content": "Fixed."},
        ]
        result = bench.run_task("@groq/qwen3-coder-480b", task, tempfile.mkdtemp(prefix="bench-"))
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["steps"], 1)
        mock_llm.SCRIPT[:] = [{"content": "I think it is fine as it is."}]
        result = bench.run_task("@groq/qwen3-coder-480b", task, tempfile.mkdtemp(prefix="bench-"))
        self.assertFalse(result["pass"])

    def test_a_limit_skips_instead_of_failing(self):
        from scorpi import bench
        mock_llm.SCRIPT[:] = [{"status": 429, "error": "Rate limit reached, try again in 20m"}]
        result = bench.run_task("@groq/qwen3-coder-480b", bench.TASKS[0], tempfile.mkdtemp(prefix="bench-"))
        self.assertTrue(result.get("skipped"), result)

    def test_automatic_pick_follows_results(self):
        from scorpi import llm, scores
        from scorpi.config import SETTINGS
        first = llm.pick_code_model()
        self.assertTrue(first.startswith("@groq/"), first)  # catalog order while nothing is known
        for _ in range(5):
            scores.record(first, "test_fail")
            scores.record(first, "job_failed")
            scores.record("@cerebras/qwen3-coder-480b", "test_pass")
        self.assertEqual(llm.pick_code_model(), "@cerebras/qwen3-coder-480b")
        SETTINGS.update({"learn_models": False})
        self.assertEqual(llm.pick_code_model(), first)
        SETTINGS.update({"learn_models": True})


if __name__ == "__main__":
    unittest.main()
