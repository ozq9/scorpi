import time
import unittest
import urllib.error

from tests.support import ROOT  # noqa: F401  (sets up the data folder)

NOW = time.time()
OLD = "2015-01-01T00:00:00Z"
NEW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 3 * 86400))


def fake_registry(url):
    pages = {
        "https://pypi.org/pypi/requests/json": {"releases": {"1.0": [{"upload_time_iso_8601": OLD}]}},
        "https://pypistats.org/api/packages/requests/recent": {"data": {"last_month": 900000000}},
        "https://pypi.org/pypi/reqeusts/json": {"releases": {"0.1": [{"upload_time_iso_8601": NEW}]}},
        "https://pypistats.org/api/packages/reqeusts/recent": {"data": {"last_month": 12}},
        "https://pypi.org/pypi/fastjsonparse-ai/json": {"releases": {"0.1": [{"upload_time_iso_8601": NEW}]}},
        "https://pypistats.org/api/packages/fastjsonparse-ai/recent": {"data": {"last_month": 40}},
        "https://registry.npmjs.org/lodash": {"time": {"created": OLD}},
        "https://api.npmjs.org/downloads/point/last-week/lodash": {"downloads": 50000000},
        "https://registry.npmjs.org/@types%2Fnode": {"time": {"created": OLD}},
        "https://api.npmjs.org/downloads/point/last-week/@types/node": {"downloads": 90000000},
    }
    if url in pages:
        return pages[url]
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


class PackagesTest(unittest.TestCase):
    def setUp(self):
        from scorpi import packages
        packages._cache.clear()
        self.saved = packages.FETCH
        packages.FETCH = fake_registry

    def tearDown(self):
        from scorpi import packages
        packages.FETCH = self.saved
        packages._cache.clear()

    def test_reading_install_commands(self):
        from scorpi.packages import installs
        self.assertEqual(installs("pip install -U requests==2.31 'flask[async]>=3' -r requirements.txt ."), [("pypi", "requests"), ("pypi", "flask")])
        self.assertEqual(installs("python -m pip install --target lib numpy && pytest"), [("pypi", "numpy")])
        self.assertEqual(installs("py -3.12 -m pip install rich"), [("pypi", "rich")])
        self.assertEqual(installs("npm i -D @types/node lodash@4 ./local-pkg"), [("npm", "@types/node"), ("npm", "lodash")])
        self.assertEqual(installs("cd web; pnpm add zod"), [("npm", "zod")])
        self.assertEqual(installs("npx create-vite@latest app"), [("npm", "create-vite")])
        self.assertEqual(installs("cargo add serde --features derive"), [("crates", "serde")])
        self.assertEqual(installs("uv add httpx"), [("pypi", "httpx")])
        self.assertEqual(installs("npm install"), [])
        self.assertEqual(installs("ls -la && git status"), [])

    def test_look_alikes(self):
        from scorpi.packages import looks_like
        self.assertEqual(looks_like("pypi", "reqeusts"), "requests")
        self.assertEqual(looks_like("pypi", "requests"), "")
        self.assertEqual(looks_like("npm", "lodahs"), "lodash")
        self.assertEqual(looks_like("npm", "react-dom"), "")
        self.assertEqual(looks_like("pypi", "my-own-tool"), "")

    def test_verdicts(self):
        from scorpi.packages import review
        self.assertEqual(review("pip install requests")["block"] + review("pip install requests")["ask"], [])
        made_up = review("pip install totally-invented-pkg-xyz")
        self.assertIn("does not exist on PyPI", made_up["block"][0])
        typo = review("pip install reqeusts")
        self.assertEqual(typo["block"], [])
        self.assertIn("one typo away from the popular 'requests'", typo["ask"][0])
        self.assertIn("first published 3 day(s) ago", typo["ask"][0])
        self.assertIn("only 12 downloads", typo["ask"][0])
        self.assertIn("first published", review("pip install fastjsonparse-ai")["ask"][0])
        self.assertEqual(review("npm i @types/node lodash")["ask"], [])

    def test_offline_does_not_block(self):
        from scorpi import packages

        def offline(url):
            raise urllib.error.URLError("no network")
        packages.FETCH = offline
        out = packages.review("pip install something-new")
        self.assertEqual((out["block"], out["ask"]), ([], []))

    def test_agent_refuses_invented_packages_and_asks_for_risky_ones(self):
        import tempfile
        from scorpi.agent import Agent
        from scorpi.config import SETTINGS
        saved = SETTINGS.get("tool_policy")
        SETTINGS.update({"tool_policy": {"run_shell": "ask"}})
        try:
            agent = Agent(None, model="none", skill="code", groups=["code"], auto_approve=True,
                          persona={"name": "Coder", "instructions": "", "workdir": tempfile.mkdtemp(), "project": ""})
            result = agent.execute({"id": "c1", "name": "run_shell", "arguments": {"command": "pip install totally-invented-pkg-xyz"}})
            self.assertFalse(result.ok)
            self.assertIn("does not exist on PyPI", result.text)

            class Unattended:
                unattended, cancelled, id, conv_id, origin, inbox = True, False, "x", "", "bench", []

                def emit(self, e):
                    pass
            agent.run = Unattended()
            result = agent.execute({"id": "c2", "name": "run_shell", "arguments": {"command": "pip install reqeusts"}})
            self.assertFalse(result.ok)  # full autonomy, yet a look-alike package still needs a person's OK
            self.assertIn("declined", result.text)
        finally:
            SETTINGS.update({"tool_policy": saved})


if __name__ == "__main__":
    unittest.main()
