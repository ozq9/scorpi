import os
import sys
import tempfile
import time
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401  (sets up the data folder)

SERVER = ("import sys, time\nprint('Serving at http://localhost:5173', flush=True)\ntime.sleep(0.5)\n"
          "print('Traceback (most recent call last):\\n  File \"app.py\", line 3, in handler\\nZeroDivisionError: division by zero', flush=True)\n"
          "time.sleep(30)\n")


class ErrorLineTest(unittest.TestCase):
    def test_spots_errors(self):
        from scorpi.crash import error_line
        self.assertEqual(error_line("Traceback (most recent call last):\n  File x\nZeroDivisionError: division by zero"),
                         "ZeroDivisionError: division by zero")
        self.assertEqual(error_line("ok\nTypeError: x.map is not a function\n    at App"), "TypeError: x.map is not a function")
        self.assertTrue(error_line("[vite] Internal server error: Failed to resolve import"))
        self.assertTrue(error_line("npm ERR! missing script: dev"))
        self.assertEqual(error_line("compiled with 0 errors\nwatching for changes"), "")
        self.assertEqual(error_line("GET / 200 3ms"), "")


class CrashTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        cls.saved = {k: SETTINGS.get(k) for k in ("key_groq", "cloud_urls", "cloud_anon", "model", "code_model", "checkpoints")}
        SETTINGS.update({"key_groq": "test", "cloud_urls": {"groq": cls.url}, "cloud_anon": False, "model": "@groq/qwen3-coder-480b",
                         "code_model": "", "checkpoints": False})

    @classmethod
    def tearDownClass(cls):
        from scorpi.config import SETTINGS
        cls.srv.shutdown()
        SETTINGS.update(cls.saved)

    def test_a_running_server_that_crashes_gets_fixed(self):
        from scorpi import agents, crash, events, gitops  # noqa: F401  (crash registers its watcher)
        agents.init()
        slug = "crash-demo"
        folder = agents.project_path(slug)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "server.py"), "w") as fh:
            fh.write(SERVER)
        term = gitops.term_start(slug, folder, '"%s" server.py' % sys.executable if os.name == "nt" else "%s server.py" % sys.executable)
        try:
            deadline = time.time() + 15
            while not term.crash and time.time() < deadline:
                time.sleep(0.1)
            self.assertTrue(term.crash, term.output)
            self.assertTrue(term.crash["running"])
            self.assertEqual(term.crash["line"], "ZeroDivisionError: division by zero")
            self.assertEqual(term.public(output=False)["crash"]["line"], "ZeroDivisionError: division by zero")
            text, shown = crash.prompt(term, folder, slug)
            self.assertIn("is still running but printed this error", text)
            self.assertIn("ZeroDivisionError", text)
            self.assertEqual(shown, "Fix it: ZeroDivisionError: division by zero")
            mock_llm.SCRIPT[:] = [{"content": "Fixed the division by zero."}]
            job = crash.fix(slug, folder, term.id)
            deadline = time.time() + 15
            while job.status == "running" and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(job.status, "done")
            self.assertIn("ZeroDivisionError", job.goal)
        finally:
            term.stop()

    def test_unknown_command(self):
        from scorpi import crash
        with self.assertRaises(ValueError):
            crash.fix("crash-demo", tempfile.mkdtemp(), "nope")


if __name__ == "__main__":
    unittest.main()
