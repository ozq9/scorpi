import os
import time
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401


class AttemptsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        SETTINGS.update({"key_groq": "t", "key_cerebras": "t", "cloud_urls": {"groq": cls.url, "cerebras": cls.url}, "cloud_anon": False,
                         "coder_backend": "scorpi", "checkpoints": True, "tool_policy": {}, "hooks": []})

    @classmethod
    def tearDownClass(cls):
        from scorpi.config import SETTINGS
        SETTINGS.update({"key_cerebras": "", "cloud_urls": {"groq": cls.url}})
        mock_llm.BY_MODEL.clear()
        cls.srv.shutdown()

    def test_two_models_compare_and_apply(self):
        from scorpi import agents, attempts
        agents.init()
        slug = "attempt-test"
        path = agents.project_path(slug)
        os.makedirs(os.path.join(path, "node_modules", "big"), exist_ok=True)
        with open(os.path.join(path, "app.py"), "w") as fh:
            fh.write("print('v1')\n")
        with open(os.path.join(path, "node_modules", "big", "x.js"), "w") as fh:
            fh.write("// dependency\n")
        mock_llm.BY_MODEL.clear()
        mock_llm.BY_MODEL["qwen3-coder-480b"] = [
            {"tool_calls": [{"name": "edit_file", "arguments": {"path": "app.py", "find": "v1", "replace": "v2 from groq"}}]},
            {"content": "groq done"}]
        mock_llm.BY_MODEL["gpt-oss-120b"] = [
            {"tool_calls": [{"name": "write_file", "arguments": {"path": "new.py", "content": "print('cerebras')\n"}}]},
            {"content": "cerebras done"}]
        rec = attempts.start(slug, "improve it", ["@groq/qwen3-coder-480b", "@cerebras/gpt-oss-120b"], title="Improve")
        self.assertEqual(len(rec["attempts"]), 2)
        copy = os.path.join(attempts.ROOT, slug, rec["id"], "1")
        self.assertTrue(os.path.isfile(os.path.join(copy, "app.py")))
        self.assertFalse(os.path.exists(os.path.join(copy, "node_modules")))  # dependencies are not copied
        deadline = time.time() + 20
        while time.time() < deadline and attempts.for_project(slug)[0]["running"]:
            time.sleep(0.1)
        latest = attempts.for_project(slug)[0]
        by_n = {a["n"]: a for a in latest["attempts"]}
        self.assertEqual(by_n[1]["status"], "done")
        self.assertEqual([f["path"] for f in by_n[1]["files"]], ["app.py"])
        self.assertEqual([f["path"] for f in by_n[2]["files"]], ["new.py"])
        with open(os.path.join(path, "app.py")) as fh:
            self.assertEqual(fh.read(), "print('v1')\n")  # the real project is untouched until one is applied
        out = attempts.apply(rec["id"], 1)
        self.assertEqual(out["files"], 1)
        with open(os.path.join(path, "app.py")) as fh:
            self.assertIn("v2 from groq", fh.read())
        self.assertFalse(os.path.exists(os.path.join(path, "new.py")))
        self.assertTrue(os.path.isfile(os.path.join(path, "node_modules", "big", "x.js")))
        attempts.discard(rec["id"])
        self.assertFalse(os.path.exists(os.path.join(attempts.ROOT, slug, rec["id"])))
        self.assertEqual(attempts.for_project(slug), [])
        # the attempt jobs are not mistaken for projects of their own
        self.assertNotIn("improve-try-1", [p["slug"] for p in agents.projects()])

    def test_needs_two_models(self):
        from scorpi import agents, attempts
        os.makedirs(agents.project_path("attempt-one"), exist_ok=True)
        with self.assertRaises(attempts.AttemptError):
            attempts.start("attempt-one", "x", ["@groq/qwen3-coder-480b", "@groq/qwen3-coder-480b"])


if __name__ == "__main__":
    unittest.main()
