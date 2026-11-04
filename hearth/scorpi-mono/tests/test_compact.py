import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401

from scorpi.compact import Compactor, tokens


def turn(steps, out_size=6000, history=0):
    msgs = [{"role": "system", "content": "system prompt"}]
    for i in range(history):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": "old message %d " % i + "x" * 3000})
    msgs.append({"role": "user", "content": "THE TASK"})
    for i in range(steps):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "c%d" % i, "name": "write_file", "arguments": {"path": "f%d.py" % i, "content": "y" * 4000}}]})
        msgs.append({"role": "tool", "name": "write_file", "tool_call_id": "c%d" % i, "content": "result %d " % i + "z" * out_size})
    return msgs


def pairs_ok(msgs):
    """Every tool message follows an assistant message that called it."""
    for i, m in enumerate(msgs):
        if m["role"] == "tool":
            j = i - 1
            while j >= 0 and msgs[j]["role"] == "tool":
                j -= 1
            calls = {c["id"] for c in msgs[j].get("tool_calls") or []}
            if m["tool_call_id"] not in calls:
                return False
    return True


class CompactorTest(unittest.TestCase):
    def test_small_turn_untouched(self):
        msgs = turn(2, 100)
        note, task = Compactor().fit(msgs, 50000, 1)
        self.assertEqual(note, "")
        self.assertEqual(task, 1)

    def test_trims_old_tool_output_first(self):
        msgs = turn(12)
        before = tokens(msgs)
        note, task = Compactor().fit(msgs, 24000, 1)
        self.assertIn("trimmed", note)
        self.assertLess(tokens(msgs), before)
        self.assertEqual(msgs[task]["content"], "THE TASK")
        self.assertTrue(pairs_ok(msgs))
        self.assertTrue(msgs[-1]["content"].startswith("result 11"))
        self.assertGreater(len(msgs[-1]["content"]), 5000)  # the newest output stays whole

    def test_summarizes_when_trimming_is_not_enough(self):
        msgs = turn(40, history=10)
        asked = []
        note, task = Compactor(lambda p: asked.append(p) or "Did files f0..f30.").fit(msgs, 9000, 11)
        self.assertTrue(asked)
        self.assertIn("summarized", note)
        self.assertEqual(msgs[task]["content"], "THE TASK")
        self.assertIn("Did files f0..f30.", msgs[task + 1]["content"])
        self.assertTrue(pairs_ok(msgs))
        self.assertLessEqual(tokens(msgs), 9000)

    def test_without_summarizer_lists_steps(self):
        msgs = turn(40)
        Compactor(None).fit(msgs, 6000, 1)
        self.assertTrue(any("Steps taken" in (m.get("content") or "") for m in msgs))
        self.assertTrue(pairs_ok(msgs))


class ReactiveCompactionTest(unittest.TestCase):
    def test_too_large_is_compacted_and_retried_on_the_same_model(self):
        from scorpi.agent import Agent
        from scorpi.config import SETTINGS
        srv, url = mock_llm.start()
        try:
            SETTINGS.update({"key_groq": "t", "cloud_urls": {"groq": url}, "cloud_anon": False})
            mock_llm.REQUESTS.clear()
            mock_llm.SCRIPT[:] = [
                {"status": 413, "error": "Request too large for model on tokens per minute (TPM): Limit 6000, Requested 31000"},
                {"content": "finished"},  # the answer (or the compaction summary, when one is needed)
                {"content": "finished"},
            ]
            notices = []
            agent = Agent(None, model="@groq/llama-3.3-70b", groups=["files"], emit=lambda e: notices.append(e))
            history = turn(30, history=0)[1:]
            text = agent.run_turn(history, "go")
            self.assertIn("finished", text)
            self.assertFalse(any(e.get("type") == "notice" and "Switched" in e.get("text", "") for e in notices))
            self.assertTrue(any(e.get("type") == "notice" and "Made room" in e.get("text", "") for e in notices), notices)
            last = mock_llm.REQUESTS[-1]["payload"]
            self.assertEqual(last["model"], "llama-3.3-70b")
            self.assertLess(len(str(last["messages"])), 6000 * 3.5)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
