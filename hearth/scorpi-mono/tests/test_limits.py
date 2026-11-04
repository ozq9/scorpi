import time
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401  (sets up the data folder)


class ParsingTest(unittest.TestCase):
    def test_durations(self):
        from scorpi.limits import duration
        self.assertAlmostEqual(duration("7m12.5s"), 432.5)
        self.assertAlmostEqual(duration("45s"), 45)
        self.assertAlmostEqual(duration("120ms"), 0.12)
        self.assertAlmostEqual(duration("2h"), 7200)
        self.assertAlmostEqual(duration("30"), 30)
        self.assertIsNone(duration("soon"))

    def test_daily_reset_times(self):
        import datetime as dt
        from scorpi.limits import next_reset
        noon_utc = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc).timestamp()
        # most providers: next midnight UTC
        self.assertEqual(dt.datetime.fromtimestamp(next_reset("groq", noon_utc) - 60, dt.timezone.utc).hour, 0)
        # Google: midnight US Pacific (07:00 UTC in summer, 08:00 in winter)
        self.assertEqual(dt.datetime.fromtimestamp(next_reset("gemini", noon_utc) - 60, dt.timezone.utc).hour, 7)
        winter = dt.datetime(2026, 12, 1, 12, 0, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(dt.datetime.fromtimestamp(next_reset("gemini", winter) - 60, dt.timezone.utc).hour, 8)


class Err(Exception):
    def __init__(self, msg, status=429, retry_after=0, headers=None):
        super().__init__(msg)
        self.status, self.retry_after, self.headers = status, retry_after, headers or {}


class RememberTest(unittest.TestCase):
    def setUp(self):
        from scorpi import limits
        limits.reset_state()

    tearDown = setUp

    def test_named_wait(self):
        from scorpi import limits
        until = limits.hit("@groq/llama-3.3-70b", Err("Rate limit reached for model. Please try again in 7m12.5s."))
        self.assertAlmostEqual(until - time.time(), 433.5, delta=3)
        self.assertTrue(limits.resting("@groq/llama-3.3-70b"))
        self.assertFalse(limits.resting("@groq/gpt-oss-120b"))  # other models of the provider stay usable

    def test_daily_limit_rests_until_reset(self):
        from scorpi import limits
        until = limits.hit("@cerebras/gpt-oss-120b", Err("Tokens per day limit exceeded"))
        self.assertAlmostEqual(until, limits.next_reset("cerebras"), delta=2)

    def test_openrouter_free_daily_limit_is_for_the_whole_account(self):
        from scorpi import limits
        limits.hit("@openrouter/deepseek-chat:free", Err("Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day"))
        self.assertTrue(limits.resting("@openrouter/qwen3-coder:free"))
        self.assertTrue(limits.provider_resting("openrouter"))

    def test_credits_used_up(self):
        from scorpi import limits
        limits.hit("@nebius/some-model", Err("Insufficient balance", status=402))
        self.assertTrue(limits.provider_resting("nebius"))

    def test_other_errors_are_not_limits(self):
        from scorpi import limits
        self.assertEqual(limits.hit("@groq/x", Err("Invalid API key", status=401)), 0)
        self.assertFalse(limits.resting("@groq/x"))

    def test_headers_of_a_good_answer(self):
        from scorpi import limits
        limits.observe("@groq/llama-3.3-70b", {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2m30s",
                                               "x-ratelimit-limit-requests": "1000"})
        self.assertAlmostEqual(limits.resting("@groq/llama-3.3-70b") - time.time(), 151, delta=3)
        limits.observe("@groq/llama-3.3-70b", {"x-ratelimit-remaining-requests": "998", "x-ratelimit-limit-requests": "1000"})
        self.assertFalse(limits.resting("@groq/llama-3.3-70b"))  # it answered: usable again
        left = limits.overview()["left"]
        self.assertEqual(left[0]["remaining"], 998)

    def test_resumes(self):
        from scorpi import limits
        limits.resume_later("job", "abc", time.time() + 100)
        self.assertTrue(limits.pending("job", "abc"))
        self.assertEqual(limits.due(time.time()), [])
        ready = limits.due(time.time() + 200)
        self.assertEqual([r["id"] for r in ready], ["abc"])
        self.assertFalse(limits.pending("job", "abc"))


class RoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        cls.saved = {k: SETTINGS.get(k) for k in ("key_groq", "key_cerebras", "cloud_urls", "cloud_anon", "model", "local_fallback", "code_model")}
        cls.saved["checkpoints"] = SETTINGS.get("checkpoints", True)
        SETTINGS.update({"key_groq": "test", "key_cerebras": "test", "cloud_urls": {"groq": cls.url, "cerebras": cls.url}, "cloud_anon": False,
                         "model": "@groq/llama-3.3-70b", "local_fallback": False})

    @classmethod
    def tearDownClass(cls):
        from scorpi import limits
        from scorpi.config import SETTINGS
        cls.srv.shutdown()
        SETTINGS.update(cls.saved)
        limits.reset_state()

    def setUp(self):
        from scorpi import limits
        limits.reset_state()
        mock_llm.REQUESTS.clear()

    def _chat(self, model):
        from scorpi import llm
        events = list(llm.chat_stream(model, [{"role": "user", "content": "hi"}]))
        return events

    def test_moves_on_and_remembers(self):
        from scorpi import limits
        mock_llm.SCRIPT[:] = [{"status": 429, "error": "Rate limit reached. Please try again in 9m."}, {"content": "hello from the other one"}]
        events = self._chat("@groq/llama-3.3-70b")
        self.assertEqual(events[-1]["content"], "hello from the other one")
        self.assertTrue(any(e["type"] == "switch" for e in events))
        self.assertTrue(limits.resting("@groq/llama-3.3-70b"))
        # next time it starts on another model straight away instead of asking the resting one again
        mock_llm.REQUESTS.clear()
        mock_llm.SCRIPT[:] = [{"content": "straight away"}]
        events = self._chat("@groq/llama-3.3-70b")
        self.assertEqual(events[-1]["content"], "straight away")
        self.assertEqual(len(mock_llm.REQUESTS), 1)
        self.assertNotEqual(mock_llm.REQUESTS[0]["payload"]["model"], "llama-3.3-70b")

    def test_all_resting_names_the_resume_time(self):
        from scorpi import limits, llm
        now = time.time()
        for pid in ("groq", "cerebras"):
            for mid in ("qwen3-coder-480b", "gpt-oss-120b"):
                limits.rest("@%s/%s" % (pid, mid), now + 7200, "test")
        limits.rest("@cerebras/llama-3.3-70b", now + 3600, "test")
        mock_llm.SCRIPT[:] = [{"status": 429, "error": "Rate limit reached. Please try again in 3h."}]
        with self.assertRaises(llm.LLMError) as caught:
            self._chat("@groq/llama-3.3-70b")
        self.assertAlmostEqual(caught.exception.resume_at, now + 3600, delta=5)
        self.assertIn("continues by itself", str(caught.exception))

    def test_chat_continues_by_itself(self):
        from scorpi import agent, db, events, limits
        now = time.time()
        for pid in ("groq", "cerebras"):
            for mid in ("qwen3-coder-480b", "gpt-oss-120b", "llama-3.3-70b"):
                limits.rest("@%s/%s" % (pid, mid), now + 600, "test")
        limits.clear("@groq/llama-3.3-70b")
        mock_llm.SCRIPT[:] = [{"status": 429, "error": "Too many requests, try again in 2h"}]
        conv = db.create_conversation(title="limits")
        run = agent.start_turn(conv["id"], "hello", model="@groq/llama-3.3-70b")
        error = [e for e in run.follow() if e["type"] == "error"]
        self.assertTrue(error and error[0]["resume_at"])
        pending = limits.pending("chat", conv["id"])
        self.assertTrue(pending)
        limits.reset_state()
        limits.resume_later("chat", conv["id"], now - 1, model="@groq/llama-3.3-70b")
        mock_llm.SCRIPT[:] = [{"content": "Picked it up again."}]
        limits.tick(time.time())
        resumed = events.active_run_for(conv["id"])
        if resumed:
            list(resumed.follow())
        messages = db.get_messages(conv["id"])
        self.assertEqual(messages[-1]["content"], "Picked it up again.")
        self.assertEqual(messages[-2]["data"].get("system"), "Continuing after the usage limits reset")

    def test_job_pauses_and_continues(self):
        from scorpi import agents, limits
        from scorpi.config import SETTINGS
        agents.init()
        SETTINGS.update({"code_model": "", "checkpoints": False})
        now = time.time()
        for pid in ("groq", "cerebras"):
            for mid in ("qwen3-coder-480b", "gpt-oss-120b", "llama-3.3-70b"):
                limits.rest("@%s/%s" % (pid, mid), now + 600, "test")
        limits.clear("@groq/llama-3.3-70b")
        mock_llm.SCRIPT[:] = [{"status": 429, "error": "Too many requests, try again in 2h"}]
        job = agents.assign("coder", "make a readme", title="Limits job", project="limits-job", model="@groq/llama-3.3-70b")
        deadline = time.time() + 15
        while job.status == "running" and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(job.status, "waiting")
        self.assertTrue(job.public()["resume_at"])
        self.assertTrue(limits.pending("job", job.id))
        limits.reset_state()
        limits.resume_later("job", job.id, now - 1)
        mock_llm.SCRIPT[:] = [{"content": "README written."}]
        limits.tick(time.time())
        deadline = time.time() + 15
        while job.status != "done" and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(job.status, "done")
        self.assertIn("README written", job.result)
        self.assertEqual(job.public()["resume_at"], 0)


if __name__ == "__main__":
    unittest.main()
