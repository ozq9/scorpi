import os
import tempfile
import unittest

from tests import mock_llm
from tests.support import FakeCtx, call


class CodingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        SETTINGS.update({"key_groq": "t", "key_cerebras": "t", "cloud_urls": {"groq": cls.url, "cerebras": cls.url},
                         "cloud_anon": False, "expert_model": ""})

    @classmethod
    def tearDownClass(cls):
        from scorpi.config import SETTINGS
        SETTINGS.update({"key_cerebras": "", "cloud_urls": {"groq": cls.url}})
        cls.srv.shutdown()

    def test_expert_comes_from_another_provider(self):
        from scorpi import coding
        self.assertTrue(coding.expert_model("@groq/qwen3-coder-480b").startswith("@cerebras/"))
        self.assertTrue(coding.expert_model("@cerebras/gpt-oss-120b").startswith("@groq/"))

    def test_ask_expert_tool_sends_files(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "bug.py"), "w") as fh:
            fh.write("def f(x):\n    return x / 0\n")
        ctx = FakeCtx(d)
        ctx.agent.model = "@groq/qwen3-coder-480b"
        mock_llm.REQUESTS.clear()
        mock_llm.SCRIPT[:] = [{"content": "Division by zero on line 2."}]
        res = call("ask_expert", ctx, question="Why does f crash?", files=["bug.py"])
        self.assertTrue(res.ok, res.text)
        self.assertIn("Division by zero", res.text)
        self.assertIn("Cerebras", res.text)
        sent = str(mock_llm.REQUESTS[-1]["payload"]["messages"])
        self.assertIn("return x / 0", sent)

    def test_review_of_uncommitted_and_new_files(self):
        from scorpi import gitops
        d = tempfile.mkdtemp()
        gitops.run(["init", "-q"], d)
        with open(os.path.join(d, "app.py"), "w") as fh:
            fh.write("print(1)\n")
        gitops.run(["add", "."], d)
        gitops.run(["-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "-m", "one"], d)
        with open(os.path.join(d, "app.py"), "w") as fh:
            fh.write("print(2)\n")
        with open(os.path.join(d, "new.py"), "w") as fh:
            fh.write("SECRET = 'abc'\n")
        mock_llm.REQUESTS.clear()
        mock_llm.SCRIPT[:] = [{"content": "Needs changes\n- **major** `new.py:1`: a secret in code."}]
        res = call("review_changes", FakeCtx(d))
        self.assertIn("Needs changes", res.text)
        sent = str(mock_llm.REQUESTS[-1]["payload"]["messages"])
        self.assertIn("+print(2)", sent)
        self.assertIn("+SECRET = ", sent)

    def test_nothing_to_review(self):
        res = call("review_changes", FakeCtx(tempfile.mkdtemp()))
        self.assertIn("Nothing to review", res.text)

    def test_command_prompts(self):
        from scorpi import coding
        self.assertIn("AGENTS.md", coding.command_prompt("init"))
        self.assertIn("The user added: focus on auth", coding.command_prompt("/review", "focus on auth"))
        self.assertEqual(coding.command_prompt("nope"), "")


if __name__ == "__main__":
    unittest.main()
