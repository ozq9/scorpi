import time
import unittest

from tests import mock_llm
from tests.support import ROOT  # noqa: F401  (sets up the data folder)


class HoursTest(unittest.TestCase):
    def test_window(self):
        from scorpi import backlog
        from scorpi.config import SETTINGS
        saved = (SETTINGS.get("backlog_window"), SETTINGS.get("backlog_hours"))
        backlog.reset_state()
        try:
            SETTINGS.update({"backlog_window": "night", "backlog_hours": [22, 7]})
            at = lambda hour: time.mktime((2026, 11, 4, hour, 30, 0, 0, 0, -1))  # noqa: E731
            self.assertTrue(backlog.open_now(at(23)))
            self.assertTrue(backlog.open_now(at(3)))
            self.assertFalse(backlog.open_now(at(12)))
            SETTINGS.update({"backlog_hours": [9, 17]})
            self.assertTrue(backlog.open_now(at(12)))
            self.assertFalse(backlog.open_now(at(20)))
            SETTINGS.update({"backlog_window": "paused"})
            self.assertFalse(backlog.open_now(at(12)))
            backlog.run_now()
            self.assertTrue(backlog.open_now(at(12)))  # "Run now" works through it whatever the hours
        finally:
            SETTINGS.update({"backlog_window": saved[0] or "night", "backlog_hours": saved[1] or [22, 7]})
            backlog.reset_state()


class QueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scorpi.config import SETTINGS
        cls.srv, cls.url = mock_llm.start()
        cls.saved = {k: SETTINGS.get(k) for k in ("key_groq", "cloud_urls", "cloud_anon", "model", "code_model", "checkpoints", "backlog_window")}
        SETTINGS.update({"key_groq": "test", "cloud_urls": {"groq": cls.url}, "cloud_anon": False, "model": "@groq/qwen3-coder-480b",
                         "code_model": "", "checkpoints": False, "backlog_window": "always"})

    @classmethod
    def tearDownClass(cls):
        from scorpi import backlog
        from scorpi.config import SETTINGS
        cls.srv.shutdown()
        SETTINGS.update(cls.saved)
        backlog.reset_state()

    def _until(self, check, seconds=20):
        deadline = time.time() + seconds
        while not check() and time.time() < deadline:
            time.sleep(0.05)
        return check()

    def test_works_through_the_queue_and_reports(self):
        from scorpi import agents, backlog, db
        agents.init()
        backlog.reset_state()
        import os
        for slug in ("queue-a", "queue-b"):
            os.makedirs(agents.project_path(slug), exist_ok=True)
        first = backlog.add("queue-a", "Add a footer")
        second = backlog.add("queue-b", "Write a README")
        third = backlog.add("queue-a", "Add dark mode")
        backlog.move(third["id"], -1)  # dark mode before the README
        self.assertEqual([i["title"] for i in backlog.overview()["items"] if i["status"] == "queued"], ["Add a footer", "Add dark mode", "Write a README"])
        mock_llm.SCRIPT[:] = [{"content": "Footer added."}, {"content": "Dark mode added."}, {"content": "README written."}]
        for _ in range(3):
            backlog.tick()
            item = next(i for i in backlog.overview()["items"] if i["status"] == "running")
            job = agents.job(item["job_id"])
            self.assertTrue(self._until(lambda: job.status != "running"))
            asked = [(m["data"] or {}).get("model_content") or m["content"] for m in db.get_messages(job.conv_id) if m["role"] == "user"]
            self.assertTrue(any("From your queue" in a for a in asked), asked)
        backlog.tick()  # the last one settles, the queue is empty: a report
        over = backlog.overview()
        self.assertEqual({i["status"] for i in over["items"]}, {"done"})
        self.assertEqual(len(over["reports"]), 1)
        self.assertEqual([i["title"] for i in over["reports"][0]["items"]], ["Add a footer", "Add dark mode", "Write a README"])
        self.assertIsNone(over["session"])
        backlog.retry(first["id"])
        self.assertEqual(next(i for i in backlog.overview()["items"] if i["id"] == first["id"])["status"], "queued")
        backlog.remove(first["id"])
        self.assertFalse(any(i["id"] == first["id"] for i in backlog.overview()["items"]))
        self.assertTrue(second)

    def test_upkeep_joins_the_queue(self):
        from scorpi import agents, backlog
        import os
        backlog.reset_state()
        os.makedirs(agents.project_path("queue-up"), exist_ok=True)
        backlog.set_upkeep("queue-up", "deps", every="week")
        state = backlog._load()
        state["upkeep"][0]["next"] = time.time() - 1
        backlog._due_upkeep(time.time())
        items = backlog.overview()["items"]
        self.assertEqual([(i["title"], i["upkeep"]) for i in items], [("Update dependencies", "deps")])
        self.assertGreater(backlog._load()["upkeep"][0]["next"], time.time())
        backlog._load()["upkeep"][0]["next"] = time.time() - 1
        backlog._due_upkeep(time.time())
        self.assertEqual(len(backlog.overview()["items"]), 1)  # not twice while the first still waits
        backlog.set_upkeep("queue-up", "deps", on=False)
        self.assertEqual(backlog.overview()["upkeep"], [])

    def test_waits_when_every_model_is_at_its_limit(self):
        from scorpi import agents, backlog, limits
        import os
        backlog.reset_state()
        os.makedirs(agents.project_path("queue-c"), exist_ok=True)
        backlog.add("queue-c", "Something")
        for mid in ("qwen3-coder-480b", "llama-3.3-70b", "gpt-oss-120b"):
            limits.rest("@groq/" + mid, time.time() + 3600, "test")
        try:
            backlog.tick()
            self.assertEqual(backlog.overview()["items"][0]["status"], "queued")
        finally:
            limits.reset_state()
            backlog.reset_state()


if __name__ == "__main__":
    unittest.main()
