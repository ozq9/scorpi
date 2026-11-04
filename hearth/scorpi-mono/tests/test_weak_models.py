import os
import tempfile
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401  (sets up the data folder)


class RepairTest(unittest.TestCase):
    def test_almost_json(self):
        from scorpi.toolcalls import parse_args, repair_json
        self.assertEqual(repair_json('{"path": "a.py", "content": "x",}'), {"path": "a.py", "content": "x"})
        self.assertEqual(repair_json("{'path': 'a.py', 'all': True}"), {"path": "a.py", "all": True})
        self.assertEqual(repair_json('```json\n{"path": "a.py"}\n```'), {"path": "a.py"})
        self.assertEqual(repair_json('"{\\"path\\": \\"a.py\\"}"'), {"path": "a.py"})
        self.assertEqual(repair_json('{"path": "a.py", "content": "line 1\nline 2'), {"path": "a.py", "content": "line 1\nline 2"})
        self.assertEqual(repair_json("{path: 'a.py'}"), {"path": "a.py"})
        self.assertEqual(repair_json(""), {})
        self.assertEqual(parse_args("not json at all"), {"_raw": "not json at all"})

    def test_names_and_parameters(self):
        from scorpi.toolcalls import fix_call
        schemas = {"read_file": {"properties": {"path": {}, "start_line": {}}},
                   "edit_file": {"properties": {"path": {}, "find": {}, "replace": {}, "edits": {}}}}
        self.assertEqual(fix_call({"name": "functions.read_file", "arguments": {"file_path": "a.py"}}, schemas)["arguments"], {"path": "a.py"})
        self.assertEqual(fix_call({"name": "ReadFile", "arguments": {}}, schemas)["name"], "read_file")
        self.assertEqual(fix_call({"name": "edit-file", "arguments": {}}, schemas)["name"], "edit_file")
        self.assertEqual(fix_call({"name": "read_fil", "arguments": {}}, schemas)["name"], "read_file")
        fixed = fix_call({"name": "edit_file", "arguments": {"arguments": {"path": "a", "old_string": "x", "new_string": "y"}}}, schemas)
        self.assertEqual(fixed["arguments"], {"path": "a", "find": "x", "replace": "y"})
        self.assertEqual(fix_call({"name": "read_file", "arguments": '{"path": "b.py"}'}, schemas)["arguments"], {"path": "b.py"})

    def test_calls_written_as_text(self):
        from scorpi.toolcalls import extract
        names = ["read_file", "write_file"]
        calls, rest = extract('Let me look.\n<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>', names)
        self.assertEqual([(c["name"], c["arguments"]) for c in calls], [("read_file", {"path": "a.py"})])
        self.assertEqual(rest, "Let me look.")
        self.assertTrue(calls[0]["id"].isalnum() and len(calls[0]["id"]) == 9)
        calls, _ = extract('<function=write_file>{"path": "b.txt", "content": "hi"}</function>', names)
        self.assertEqual(calls[0]["arguments"], {"path": "b.txt", "content": "hi"})
        calls, _ = extract('```json\n{"name": "read_file", "parameters": {"path": "c.py"}}\n```', names)
        self.assertEqual(calls[0]["arguments"], {"path": "c.py"})
        calls, rest = extract('{"name": "read_file", "arguments": {"path": "d.py"}}', names)
        self.assertEqual((calls[0]["arguments"], rest), ({"path": "d.py"}, ""))
        calls, _ = extract('[TOOL_CALLS] [{"name": "read_file", "arguments": {"path": "e.py"}}]', names)
        self.assertEqual(calls[0]["arguments"], {"path": "e.py"})
        # ordinary JSON in an answer is not a call
        text = 'The config is {"name": "my-app", "version": 2}.'
        self.assertEqual(extract(text, names), ([], text))

    def test_hidden_while_streaming(self):
        from scorpi.toolcalls import Hide
        veil = Hide()
        shown = "".join(veil.feed(t) for t in ["Look", "ing.<tool", "_call>{\"name\": \"x\"", "}</tool_", "call> done"]) + veil.flush()
        self.assertEqual(shown, "Looking. done")
        veil = Hide()
        self.assertEqual("".join(veil.feed(t) for t in ["a < b", " and <t", "able>"]) + veil.flush(), "a < b and <table>")


class GuardTest(unittest.TestCase):
    def test_same_error_again_and_again(self):
        from scorpi.guard import LoopGuard
        guard = LoopGuard()
        out = "FAIL: test_add (tests.test_calc.T)\nAssertionError: 5 != -1\nFAILED (failures=1)"
        self.assertIsNone(guard.observe("run_tests", {}, False, out))
        self.assertIsNone(guard.observe("run_tests", {}, False, out.replace("-1", "-3")))
        level, text = guard.observe("run_tests", {}, False, out)
        self.assertEqual(level, "nudge")
        self.assertIn("came back 3 times", text)
        guard.observe("run_tests", {}, False, out)
        self.assertEqual(guard.observe("run_tests", {}, False, out)[0], "stuck")

    def test_back_and_forth(self):
        from scorpi.guard import LoopGuard
        folder = tempfile.mkdtemp()
        guard = LoopGuard()
        path = os.path.join(folder, "a.py")
        verdicts = []
        for body in ("x = 1\n", "x = 2\n", "x = 1\n"):
            with open(path, "w") as fh:
                fh.write(body)
            verdicts.append(guard.observe("write_file", {"path": "a.py"}, True, "Wrote a.py", folder))
        self.assertEqual(verdicts[:2], [None, None])
        self.assertEqual(verdicts[2][0], "nudge")
        self.assertIn("back and forth", verdicts[2][1])


class AgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        cls.saved = {k: SETTINGS.get(k) for k in ("key_groq", "key_cerebras", "cloud_urls", "cloud_anon", "model", "code_model",
                                                    "tool_policy", "step_routing", "expert_model")}
        SETTINGS.update({"key_groq": "groq-key", "key_cerebras": "cerebras-key", "cloud_urls": {"groq": cls.url, "cerebras": cls.url},
                         "cloud_anon": False, "model": "@groq/qwen3-coder-480b", "code_model": "", "expert_model": "",
                         "tool_policy": {"run_tests": "allow", "write_file": "allow"}, "step_routing": "smart"})

    @classmethod
    def tearDownClass(cls):
        from scorpi import limits, scores
        from scorpi.config import SETTINGS
        cls.srv.shutdown()
        SETTINGS.update(cls.saved)
        limits.reset_state()
        scores.reset_state()

    def setUp(self):
        from scorpi import limits, scores
        limits.reset_state()
        scores.reset_state()
        mock_llm.REQUESTS.clear()

    def _agent(self, folder, **kw):
        from scorpi.agent import Agent
        return Agent(None, model="@groq/qwen3-coder-480b", skill="code", groups=["code", "files"],
                     persona={"name": "Coder", "instructions": "", "workdir": folder, "project": ""}, max_steps=12, **kw)

    def test_sloppy_arguments_still_work(self):
        folder = tempfile.mkdtemp(prefix="weak-")
        mock_llm.SCRIPT[:] = [
            {"tool_calls": [{"name": "WriteFile", "raw": "{'file_path': 'hello.txt', 'content': 'hi there',}"}]},
            {"content": "Wrote it."},
        ]
        agent = self._agent(folder)
        agent.run_turn([{"role": "user", "content": "write hello.txt"}], "write hello.txt")
        with open(os.path.join(folder, "hello.txt")) as fh:
            self.assertEqual(fh.read(), "hi there")

    def test_unreadable_arguments_are_explained(self):
        folder = tempfile.mkdtemp(prefix="weak-")
        mock_llm.SCRIPT[:] = [{"tool_calls": [{"name": "write_file", "raw": "path is hello.txt please"}]}, {"content": "Sorry."}]
        agent = self._agent(folder)
        agent.run_turn([{"role": "user", "content": "write hello.txt"}], "write hello.txt")
        tool = next(p for p in agent.parts if p["type"] == "tool")
        self.assertFalse(tool["ok"])
        self.assertIn("could not be read as JSON", tool["result"])

    def test_text_tool_format_for_models_without_tool_calling(self):
        folder = tempfile.mkdtemp(prefix="weak-")
        mock_llm.SCRIPT[:] = [
            {"status": 400, "error": "This model does not support tools."},
            {"status": 400, "error": "This model does not support tools."},  # asked again without the usage report
            {"content": 'I will write it.\n<tool_call>{"name": "write_file", "arguments": {"path": "t.txt", "content": "via text"}}</tool_call>'},
            {"content": "Done, t.txt is written."},
        ]
        agent = self._agent(folder)
        text = agent.run_turn([{"role": "user", "content": "write t.txt"}], "write t.txt")
        self.assertTrue(agent.text_tools)
        with open(os.path.join(folder, "t.txt")) as fh:
            self.assertEqual(fh.read(), "via text")
        later = mock_llm.REQUESTS[2]["payload"]
        self.assertNotIn("tools", later)
        self.assertIn("<tool_call>", later["messages"][0]["content"])
        last = mock_llm.REQUESTS[3]["payload"]["messages"]
        self.assertTrue(any(m["role"] == "user" and "<tool_result name=\"write_file\">" in (m["content"] or "") for m in last))
        self.assertFalse(any(m["role"] == "tool" for m in last))
        self.assertNotIn("<tool_call>", text)

    def test_call_written_in_the_answer_runs(self):
        folder = tempfile.mkdtemp(prefix="weak-")
        mock_llm.SCRIPT[:] = [
            {"content": '<tool_call>{"name": "write_file", "arguments": {"path": "w.txt", "content": "ok"}}</tool_call>'},
            {"content": "Written."},
        ]
        agent = self._agent(folder)
        agent.run_turn([{"role": "user", "content": "write w.txt"}], "write w.txt")
        self.assertTrue(os.path.isfile(os.path.join(folder, "w.txt")))
        self.assertFalse(agent.text_tools)  # it can call tools; it just wrote this one as text

    def test_stronger_model_takes_over_when_tests_keep_failing(self):
        folder = tempfile.mkdtemp(prefix="weak-")
        os.makedirs(os.path.join(folder, "tests"))
        with open(os.path.join(folder, "tests", "test_x.py"), "w") as fh:
            fh.write("import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(1, 2)\n")
        runs = [{"tool_calls": [{"name": "run_tests", "arguments": {"timeout": 60 + i}}]} for i in range(4)]  # "fixes" in between
        mock_llm.SCRIPT[:] = runs + [{"content": "Handing over."}, {"content": "Fixed by the stronger model."}]
        agent = self._agent(folder)
        agent.run_turn([{"role": "user", "content": "make the tests pass"}], "make the tests pass")
        self.assertEqual(agent.escalations, 1)
        keys = [r["headers"].get("Authorization", "") for r in mock_llm.REQUESTS]
        self.assertTrue(all("groq-key" in k for k in keys[:4]), keys)
        self.assertIn("cerebras-key", keys[-1])
        sent = mock_llm.REQUESTS[-1]["payload"]["messages"]
        self.assertTrue(any("stronger model takes over" in (m.get("content") or "") for m in sent))
        self.assertTrue(any("Scorpi noticed a loop" in (m.get("content") or "") for m in sent))

    def test_thrifty_starts_fast(self):
        from scorpi import llm
        from scorpi.config import SETTINGS
        SETTINGS.update({"step_routing": "thrifty", "fast_model": "@cerebras/llama-3.3-70b"})
        try:
            agent = self._agent(tempfile.mkdtemp(prefix="weak-"))
            self.assertEqual(agent.model, "@cerebras/llama-3.3-70b")
            self.assertEqual(agent.ladder, ["@groq/qwen3-coder-480b"])
            self.assertEqual(llm.fast_model(), "@cerebras/llama-3.3-70b")
        finally:
            SETTINGS.update({"step_routing": "smart", "fast_model": ""})


if __name__ == "__main__":
    unittest.main()
