"""Start Hearth: python server.py  (then open http://localhost:8000)."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hearth.config import DATA_DIR  # noqa: E402

if sys.stdout is None or sys.stderr is None:
    # started with pythonw (no console): keep a log file instead
    _log = open(os.path.join(DATA_DIR, "server.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

from hearth.server import serve  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hearth local AI server")
    parser.add_argument("--host", default="0.0.0.0", help="Interface to bind (0.0.0.0 = reachable from LAN/Tailscale)")
    parser.add_argument("--port", type=int, default=None, help="HTTP port (default 8000)")
    parser.add_argument("--https-port", type=int, default=None, help="HTTPS port (default 8443, 0 disables)")
    args = parser.parse_args()
    serve(args.host, args.port, args.https_port)
