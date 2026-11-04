import os
import tempfile
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401  (sets up the data folder)

KEY = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


class RedactTest(unittest.TestCase):
    def test_secrets_become_placeholders_and_come_back(self):
        from scorpi import privacy
        text = ("OPENAI_API_KEY=%s\nDEBUG=true\nDB_PASSWORD='hunter2hunter2'\nNAME=my-app\nTOKEN=${GITHUB_TOKEN}\n"
                "ghp_%s\n-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----" % (KEY, "x" * 36))
        clean, n = privacy.redact(text)
        self.assertEqual(n, 4)
        for secret in (KEY, "hunter2hunter2", "ghp_" + "x" * 36, "BEGIN RSA PRIVATE KEY"):
            self.assertNotIn(secret, clean)
        for kept in ("DEBUG=true", "NAME=my-app", "TOKEN=${GITHUB_TOKEN}"):
            self.assertIn(kept, clean)
        self.assertEqual(privacy.redact(text)[0], clean)  # the same secret always gets the same placeholder
        self.assertEqual(privacy.restore({"content": clean, "list": [clean]}), {"content": text, "list": [text]})

    def test_requests_and_tool_calls(self):
        from scorpi import privacy
        messages = [{"role": "user", "content": "use key " + KEY},
                    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"content": "KEY=" + KEY}}]}]
        out, n = privacy.outgoing(messages)
        self.assertEqual(n, 2)
        self.assertNotIn(KEY, str(out))
        self.assertIn(KEY, str(messages))  # the original stays as it was

    def test_scorpis_own_keys_never_leave(self):
        from scorpi import privacy
        from scorpi.config import SETTINGS
        saved = SETTINGS.get("key_cerebras")
        SETTINGS.update({"key_cerebras": "plainlookingkey12345"})
        try:
            self.assertNotIn("plainlookingkey12345", privacy.redact("the key is plainlookingkey12345")[0])
        finally:
            SETTINGS.update({"key_cerebras": saved or ""})


class ProvidersTest(unittest.TestCase):
    def test_data_use(self):
        from scorpi import privacy
        from scorpi.config import SETTINGS
        self.assertTrue(privacy.allowed("groq"))
        self.assertFalse(privacy.allowed("gemini"))
        self.assertFalse(privacy.allowed("llm7"))  # unclear terms count as not private
        SETTINGS.update({"privacy_trusted": ["gemini"]})
        try:
            self.assertTrue(privacy.allowed("gemini"))
        finally:
            SETTINGS.update({"privacy_trusted": []})


class PrivateWorkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        cls.saved = {k: SETTINGS.get(k) for k in ("key_groq", "key_gemini", "cloud_urls", "cloud_anon", "model", "code_model", "tool_policy", "private_mode")}
        SETTINGS.update({"key_groq": "test", "key_gemini": "test", "cloud_urls": {"groq": cls.url, "gemini": cls.url}, "cloud_anon": False,
                         "model": "@groq/qwen3-coder-480b", "code_model": "", "tool_policy": {"write_file": "allow"}, "private_mode": False})

    @classmethod
    def tearDownClass(cls):
        from scorpi.config import SETTINGS
        cls.srv.shutdown()
        SETTINGS.update(cls.saved)

    def setUp(self):
        mock_llm.REQUESTS.clear()

    def test_private_scope_only_uses_private_providers(self):
        from scorpi import llm, privacy
        self.assertIn("gemini", llm.cloud_order())
        with privacy.scope(True):
            self.assertIn("groq", llm.cloud_order())
            self.assertNotIn("gemini", llm.cloud_order())
            mock_llm.SCRIPT[:] = [{"content": "private answer"}]
            events = list(llm.chat_stream("@gemini/gemini-2.5-flash", [{"role": "user", "content": "hi"}]))
        self.assertEqual(events[0]["type"], "switch")
        self.assertTrue(events[0]["model"].startswith("@groq/"))
        self.assertEqual(events[-1]["content"], "private answer")

    def test_private_project_agent_and_secrets(self):
        from scorpi import privacy
        from scorpi.agent import Agent
        folder = tempfile.mkdtemp(prefix="private-")
        privacy.set_project_private("secret-proj", True)
        mock_llm.SCRIPT[:] = [{"tool_calls": [{"name": "write_file", "arguments": {"path": ".env", "content": "OPENAI_API_KEY=__PLACEHOLDER__\n"}}]},
                              {"content": "Saved."}]
        agent = Agent(None, model="@gemini/gemini-2.5-flash", skill="code", groups=["code", "files"],
                      persona={"name": "Coder", "instructions": "", "workdir": folder, "project": "secret-proj"}, max_steps=4)
        self.assertTrue(agent.private)
        # the model only ever sees the placeholder; give it back as the model would
        placeholder = privacy.redact(KEY)[0]
        mock_llm.SCRIPT[0]["tool_calls"][0]["arguments"]["content"] = "OPENAI_API_KEY=%s\n" % placeholder
        agent.run_turn([{"role": "user", "content": "save my key %s in .env" % KEY}], "save my key")
        sent = str([r["payload"] for r in mock_llm.REQUESTS])
        self.assertNotIn(KEY, sent)
        self.assertIn(placeholder, sent)
        self.assertTrue(all(r["payload"]["model"] != "gemini-2.5-flash" for r in mock_llm.REQUESTS))
        with open(os.path.join(folder, ".env")) as fh:
            self.assertEqual(fh.read(), "OPENAI_API_KEY=%s\n" % KEY)
        privacy.set_project_private("secret-proj", False)


if __name__ == "__main__":
    unittest.main()
