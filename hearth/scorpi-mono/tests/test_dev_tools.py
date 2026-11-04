import os
import tempfile
import textwrap
import time
import unittest

from tests.support import FakeCtx, call


def write(folder, rel, text):
    path = os.path.join(folder, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="proj-")
        self.ctx = FakeCtx(self.dir)


class PathsTest(ProjectCase):
    def test_relative_paths_land_in_the_project(self):
        res = call("write_file", self.ctx, path="src/app.py", content="print('hi')\n")
        self.assertTrue(res.ok, res.text)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "src", "app.py")))
        self.assertIn("Checks passed", res.text)


class EditTest(ProjectCase):
    def test_unique_match_required(self):
        p = write(self.dir, "a.py", "x = 1\nx = 1\n")
        res = call("edit_file", self.ctx, path=p, find="x = 1", replace="x = 2")
        self.assertFalse(res.ok)
        self.assertIn("occurs 2 times", res.text)
        res = call("edit_file", self.ctx, path=p, find="x = 1", replace="x = 2", all=True)
        self.assertTrue(res.ok, res.text)
        self.assertEqual(read(p), "x = 2\nx = 2\n")

    def test_line_numbers_from_read_file_are_ignored(self):
        p = write(self.dir, "b.py", "def f():\n    return 1\n")
        res = call("edit_file", self.ctx, path=p, find="    2\t    return 1", replace="    2\t    return 2")
        self.assertTrue(res.ok, res.text)
        self.assertIn("return 2", read(p))

    def test_indentation_tolerant_and_reindented(self):
        p = write(self.dir, "c.py", "class A:\n    def f(self):\n        return 1\n")
        res = call("edit_file", self.ctx, path=p, find="def f(self):\n    return 1", replace="def f(self):\n    return 2")
        self.assertTrue(res.ok, res.text)
        self.assertEqual(read(p), "class A:\n    def f(self):\n        return 2\n")
        self.assertIn("indentation", res.text)

    def test_crlf_preserved(self):
        p = write(self.dir, "d.txt", "one\r\ntwo\r\n")
        res = call("edit_file", self.ctx, path=p, find="one\ntwo", replace="one\nthree")
        self.assertTrue(res.ok, res.text)
        self.assertEqual(read(p), "one\r\nthree\r\n")

    def test_multi_edit_is_atomic(self):
        p = write(self.dir, "e.py", "a = 1\nb = 2\n")
        res = call("edit_file", self.ctx, path=p, edits=[{"find": "a = 1", "replace": "a = 10"}, {"find": "zzz", "replace": "y"}])
        self.assertFalse(res.ok)
        self.assertEqual(read(p), "a = 1\nb = 2\n")
        res = call("edit_file", self.ctx, path=p, edits=[{"find": "a = 1", "replace": "a = 10"}, {"find": "b = 2", "replace": "b = 20"}])
        self.assertTrue(res.ok, res.text)
        self.assertEqual(read(p), "a = 10\nb = 20\n")
        self.assertEqual(res.display["type"], "diff")

    def test_not_found_shows_closest_text(self):
        p = write(self.dir, "f.py", "def greet(name):\n    print('hello', name)\n")
        res = call("edit_file", self.ctx, path=p, find="def greet(nme):\n    print('hello', nme)", replace="x")
        self.assertFalse(res.ok)
        self.assertIn("most similar text", res.text)

    def test_syntax_error_reported(self):
        p = write(self.dir, "g.py", "x = 1\n")
        res = call("edit_file", self.ctx, path=p, find="x = 1", replace="x = (1")
        self.assertFalse(res.ok)
        self.assertIn("PROBLEMS FOUND", res.text)

    def test_stale_file_detected(self):
        p = write(self.dir, "h.py", "x = 1\n")
        call("read_file", self.ctx, path=p)
        time.sleep(0.02)
        write(self.dir, "h.py", "x = 5\n")
        os.utime(p, (time.time() + 5, time.time() + 5))
        res = call("edit_file", self.ctx, path=p, find="x = 5", replace="x = 6")
        self.assertFalse(res.ok)
        self.assertIn("changed on disk", res.text)


class ReadTest(ProjectCase):
    def test_numbered_and_paged(self):
        p = write(self.dir, "r.txt", "\n".join("line %d" % i for i in range(1, 11)) + "\n")
        res = call("read_file", self.ctx, path=p, start_line=3, max_lines=2)
        self.assertIn("    3\tline 3", res.text)
        self.assertIn("start_line=5", res.text)


class SearchTest(ProjectCase):
    def setUp(self):
        super().setUp()
        write(self.dir, "src/app.ts", "export function loadUser(id: string) {\n  return fetch(id)\n}\n")
        write(self.dir, "src/util.js", "const loadUserCache = () => {}\n")
        write(self.dir, "node_modules/lib/index.js", "function loadUser() {}\n")
        write(self.dir, "README.md", "# Demo\n## Setup\n")

    def test_grep_skips_dependencies(self):
        res = call("grep", self.ctx, pattern=r"loadUser\b")
        self.assertIn("src/app.ts:1:", res.text)
        self.assertNotIn("node_modules", res.text)
        res = call("grep", self.ctx, pattern="loaduser", ignore_case=True, glob="*.js", output="files")
        self.assertIn("src/util.js", res.text)
        self.assertNotIn("app.ts", res.text)

    def test_grep_bad_regex(self):
        res = call("grep", self.ctx, pattern="fetch(")
        self.assertFalse(res.ok)

    def test_glob(self):
        res = call("glob", self.ctx, pattern="src/**/*.{ts,js}")
        self.assertIn("src/app.ts", res.text)
        self.assertIn("src/util.js", res.text)
        self.assertNotIn("node_modules", res.text)

    def test_repo_map(self):
        write(self.dir, "pkg/models.py", "class User:\n    def __init__(self, name):\n        pass\n    def save(self):\n        pass\n\ndef load(path, *, strict=False):\n    pass\n")
        res = call("repo_map", self.ctx)
        self.assertIn("pkg/models.py", res.text)
        self.assertIn("class User: __init__, save", res.text)
        self.assertIn("def load(path, strict)", res.text)
        self.assertIn("function loadUser(id: string)", res.text)
        self.assertIn("Setup", res.text)


class PatchTest(ProjectCase):
    def test_codex_patch(self):
        p = write(self.dir, "m.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n")
        patch = textwrap.dedent("""\
            *** Begin Patch
            *** Update File: m.py
            @@
             def a():
            -    return 1
            +    return 11
            @@
             def b():
            -    return 2
            +    return 22
            *** Add File: n.py
            +print('new')
            *** End Patch
            """)
        res = call("apply_patch", self.ctx, patch=patch)
        self.assertTrue(res.ok, res.text)
        self.assertIn("return 11", read(p))
        self.assertIn("return 22", read(p))
        self.assertEqual(read(os.path.join(self.dir, "n.py")), "print('new')\n")

    def test_unified_diff_and_atomicity(self):
        p = write(self.dir, "u.txt", "alpha\nbeta\ngamma\n")
        good = "--- a/u.txt\n+++ b/u.txt\n@@ -1,3 +1,3 @@\n alpha\n-beta\n+BETA\n gamma\n"
        bad = good + "--- a/u.txt\n+++ b/u.txt\n@@ -1,1 +1,1 @@\n-nothere\n+x\n"
        res = call("apply_patch", self.ctx, patch=bad)
        self.assertFalse(res.ok)
        self.assertEqual(read(p), "alpha\nbeta\ngamma\n")
        res = call("apply_patch", self.ctx, patch=good)
        self.assertTrue(res.ok, res.text)
        self.assertEqual(read(p), "alpha\nBETA\ngamma\n")


class ChecksTest(ProjectCase):
    def test_html_missing_asset_and_json(self):
        write(self.dir, "index.html", '<link rel="stylesheet" href="style.css"><script src="app.js"></script><a href="/about">x</a>')
        write(self.dir, "app.js", "console.log(1)\n")
        write(self.dir, "data.json", "{bad json}")
        res = call("check_code", self.ctx)
        self.assertFalse(res.ok)
        self.assertIn("style.css", res.text)
        self.assertNotIn("app.js, which", res.text)
        self.assertIn("data.json", res.text)

    def test_js_syntax(self):
        write(self.dir, "bad.js", "function (\n")
        res = call("check_code", self.ctx, path="bad.js")
        self.assertFalse(res.ok)


class TestsAndProcesses(ProjectCase):
    def test_detect_and_run_unittest(self):
        write(self.dir, "tests/test_x.py", "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(1, 2)\n")
        from scorpi.tools.dev import detect_test_command
        cmd = detect_test_command(self.dir)
        self.assertIn("unittest", cmd)
        res = call("run_tests", self.ctx)
        self.assertFalse(res.ok)
        self.assertIn("FAILED", res.text)

    def test_background_process(self):
        res = call("run_shell", self.ctx, command="echo serving at http://localhost:4321/ ; sleep 30", background=True)
        self.assertTrue(res.ok, res.text)
        self.assertIn("http://localhost:4321/", res.text)
        pid = res.text.split("process ")[1].split(",")[0]
        out = call("process_output", self.ctx, id=pid)
        self.assertIn("running", out.text)
        stop = call("process_stop", self.ctx, id=pid)
        self.assertIn("Stopped", stop.text)


class GitReadTest(ProjectCase):
    def test_status_and_log(self):
        from scorpi import gitops
        gitops.run(["init", "-q"], self.dir)
        gitops.run(["-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "first"], self.dir)
        write(self.dir, "x.txt", "hi\n")
        self.assertIn("x.txt", call("git_read", self.ctx, action="status").text)
        self.assertIn("first", call("git_read", self.ctx, action="log").text)
        self.assertFalse(call("git_read", self.ctx, action="diff", ref="--output=/tmp/x").ok)


if __name__ == "__main__":
    unittest.main()
