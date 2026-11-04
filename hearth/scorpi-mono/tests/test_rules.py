import os
import tempfile
import unittest

from tests.support import ROOT  # noqa: F401

from scorpi.agents import project_rules


def put(folder, rel, text):
    path = os.path.join(folder, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class RulesTest(unittest.TestCase):
    def test_every_convention_is_read_once(self):
        d = tempfile.mkdtemp()
        put(d, "AGENTS.md", "Use tabs.")
        put(d, "CLAUDE.md", "Use tabs.")  # same text: read once
        put(d, ".cursor/rules/style.mdc", "---\nglobs: src/**/*.ts\nalwaysApply: false\n---\nPrefer interfaces.")
        put(d, ".cursor/rules/always.mdc", "---\nalwaysApply: true\n---\nWrite tests.")
        put(d, ".clinerules", "No emojis.")
        put(d, "api/AGENTS.md", "API rules")
        put(d, "node_modules/x/AGENTS.md", "ignored")
        names, text = project_rules(d)
        self.assertEqual(text.count("Use tabs."), 1)
        self.assertIn("Prefer interfaces.", text)
        self.assertIn("applies to files matching src/**/*.ts", text)
        self.assertIn("Write tests.", text)
        self.assertIn("No emojis.", text)
        self.assertIn("api/AGENTS.md", text)
        self.assertNotIn("node_modules", text)
        self.assertIn("AGENTS.md", names)

    def test_none_without_rules(self):
        self.assertIsNone(project_rules(tempfile.mkdtemp()))


if __name__ == "__main__":
    unittest.main()
