"""Shared test setup: Scorpi's data goes to a throwaway folder before any scorpi module is imported."""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if not os.environ.get("SCORPI_TEST_DATA"):
    os.environ["SCORPI_TEST_DATA"] = tempfile.mkdtemp(prefix="scorpi-test-")
os.environ["SCORPI_DATA"] = os.environ["SCORPI_TEST_DATA"]

from scorpi import tools  # noqa: E402

tools.load_all()


class FakeAgent:
    """What tools read from ctx.agent."""

    def __init__(self, workdir="", project=""):
        self.seen = {}
        self.workdir = workdir
        self.project = project
        self.persona = {"workdir": workdir, "project": project} if workdir else None


class FakeCtx:
    def __init__(self, workdir=""):
        self.agent = FakeAgent(workdir)
        self.run = None
        self.conv_id = ""

    def progress(self, text):
        pass


def call(name, ctx=None, **args):
    """Run a registered tool the way the agent does (inside its project folder). Returns the Result."""
    from scorpi.tools import TOOLS, ToolError, base_dir, Result
    ctx = ctx or FakeCtx()
    with base_dir(ctx.agent.workdir):
        try:
            return TOOLS[name]["fn"](ctx, **args)
        except ToolError as exc:
            return Result("Error: %s" % exc, ok=False)
