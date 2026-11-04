import os
import tempfile
import unittest

from tests import mock_llm
from tests.support import ROOT, FakeCtx, call  # noqa: F401  (sets up the data folder)

BROKEN = "<!doctype html><title>Bakery</title><h1>Fresh bread</h1><script>undefinedFunction();</script>"
FIXED = "<!doctype html><title>Bakery</title><h1>Fresh bread</h1><p>Open daily.</p>"


def browser():
    from scorpi.tools import visual
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return bool(visual.browser_binary())


@unittest.skipUnless(browser(), "no browser on this machine")
class VisualTest(unittest.TestCase):
    def test_finds_errors_and_keeps_a_before_picture(self):
        folder = tempfile.mkdtemp(prefix="site-")
        with open(os.path.join(folder, "index.html"), "w") as fh:
            fh.write(BROKEN)
        ctx = FakeCtx(folder)
        ctx.agent.project = "visual-site"

        class Run:
            id = "run-one"
        ctx.agent.run = Run()
        first = call("visual_check", ctx)
        self.assertFalse(first.ok)
        self.assertIn("undefinedFunction", first.text)
        self.assertEqual(first.display["type"], "visual")
        self.assertTrue(first.images)
        self.assertEqual(first.display["before"], "")
        with open(os.path.join(folder, "index.html"), "w") as fh:
            fh.write(FIXED)
        Run.id = "run-two"
        second = call("visual_check", ctx)
        self.assertTrue(second.ok, second.text)
        self.assertIn("No errors", second.text)
        self.assertTrue(second.display["before"].endswith(first.display["after"].rsplit("/", 1)[1]))  # the earlier run's picture

    def test_phone_width_and_sideways_scroll(self):
        folder = tempfile.mkdtemp(prefix="site-")
        with open(os.path.join(folder, "index.html"), "w") as fh:
            fh.write("<!doctype html><title>Wide</title><div style='width:900px'>wide</div>")
        result = call("visual_check", FakeCtx(folder), width=390, height=700)
        self.assertEqual(result.display["size"], [390, 700])

    def test_agent_checks_the_page_before_finishing(self):
        from scorpi.agent import Agent
        from scorpi.config import SETTINGS
        srv, url = mock_llm.start()
        saved = {k: SETTINGS.get(k) for k in ("key_groq", "cloud_urls", "cloud_anon", "model", "code_model", "tool_policy")}
        SETTINGS.update({"key_groq": "test", "cloud_urls": {"groq": url}, "cloud_anon": False, "model": "@groq/qwen3-coder-480b",
                         "code_model": "", "tool_policy": {"write_file": "allow"}})
        try:
            folder = tempfile.mkdtemp(prefix="site-")
            mock_llm.REQUESTS.clear()
            mock_llm.SCRIPT[:] = [
                {"tool_calls": [{"name": "write_file", "arguments": {"path": "index.html", "content": BROKEN}}]},
                {"content": "The bakery page is ready."},
                {"tool_calls": [{"name": "write_file", "arguments": {"path": "index.html", "content": FIXED}}]},
                {"content": "Fixed the script error."},
            ]
            agent = Agent(None, model="@groq/qwen3-coder-480b", skill="code", groups=["code", "files"],
                          persona={"name": "Coder", "instructions": "", "workdir": folder, "project": "bakery-site"}, max_steps=8)
            text = agent.run_turn([{"role": "user", "content": "make a bakery page"}], "make a bakery page")
            self.assertIn("Fixed the script error", text)
            checks = [p for p in agent.parts if p.get("type") == "tool" and p["name"] == "visual_check"]
            self.assertEqual(len(checks), 2)  # after the broken page, and again after the fix
            self.assertFalse(checks[0]["ok"])
            self.assertTrue(checks[1]["ok"])
            sent = [m.get("content") or "" for m in mock_llm.REQUESTS[2]["payload"]["messages"]]
            self.assertTrue(any("Scorpi opened the page" in t and "undefinedFunction" in t for t in sent))
        finally:
            srv.shutdown()
            SETTINGS.update(saved)


if __name__ == "__main__":
    unittest.main()
