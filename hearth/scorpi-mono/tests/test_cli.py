import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from tests import mock_llm
from tests.support import ROOT  # noqa: F401


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        SETTINGS.update({"key_groq": "t", "cloud_urls": {"groq": cls.url}, "cloud_anon": False, "model": "@groq/qwen3-coder-480b",
                         "code_model": "", "autonomy": "auto", "tool_policy": {}})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def run_cli(self, argv, stdin=""):
        from scorpi import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(stdin)), \
                mock.patch("builtins.input", side_effect=list(stdin.splitlines()) + [EOFError()]):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_print_mode_does_the_task(self):
        folder = tempfile.mkdtemp(prefix="cli-")
        mock_llm.SCRIPT[:] = [
            {"tool_calls": [{"name": "write_file", "arguments": {"path": "hello.py", "content": "print('hi')\n"}}]},
            {"tool_calls": [{"name": "run_shell", "arguments": {"command": "python3 hello.py"}}]},
            {"content": "Created hello.py and ran it."},
        ]
        code, out, err = self.run_cli(["-p", "make hello.py", "-C", folder])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "Created hello.py and ran it.")
        self.assertTrue(os.path.isfile(os.path.join(folder, "hello.py")))
        self.assertIn("Writing hello.py", err)
        self.assertIn("exit 0", err)  # the command ran without a question (autonomy auto)
        from scorpi import agents
        self.assertIn(os.path.realpath(folder), [os.path.realpath(p) for p in agents.links().values()])

    def test_print_mode_declines_what_needs_a_question(self):
        folder = tempfile.mkdtemp(prefix="cli-")
        mock_llm.SCRIPT[:] = [
            {"tool_calls": [{"name": "run_shell", "arguments": {"command": "curl https://example.com"}}]},
            {"content": "Could not fetch it."},
        ]
        code, out, err = self.run_cli(["-p", "fetch it", "-C", folder])
        self.assertIn("Declined run_shell", err)
        self.assertIn("Could not fetch it.", out)

    def test_interactive_plan_approval_and_commands(self):
        folder = tempfile.mkdtemp(prefix="cli-")
        mock_llm.SCRIPT[:] = [
            {"tool_calls": [{"name": "submit_plan", "arguments": {"plan": "1. Create notes.txt\n2. Done", "title": "Notes"}}]},
            {"tool_calls": [{"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello\n"}}]},
            {"content": "Built it."},
        ]
        code, out, err = self.run_cli(["-C", folder, "--plan", "make notes"], stdin="y\n/cost\n/ask\n/exit\n")
        self.assertEqual(code, 0)
        self.assertIn("Plan: Notes", out)
        self.assertIn("Built it.", out)
        self.assertTrue(os.path.isfile(os.path.join(folder, "notes.txt")))
        self.assertIn("calls", out)  # /cost
        self.assertIn("asks before every command", out)


if __name__ == "__main__":
    unittest.main()
