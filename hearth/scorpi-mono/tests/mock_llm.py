"""A tiny OpenAI-compatible server for tests: /models and streaming /chat/completions (scripted replies)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT = []  # list of replies: {"content": str} or {"tool_calls": [{"name", "arguments" | "raw"}]}, optional "status", "error", "headers"
BY_MODEL = {}  # model id -> its own list of replies (parallel agents on different models)
REQUESTS = []
MODELS = {"data": [{"id": "qwen3-coder-480b"}, {"id": "llama-3.3-70b"}, {"id": "gpt-oss-120b"}]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps(MODELS).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        REQUESTS.append({"path": self.path, "payload": payload, "headers": dict(self.headers)})
        own = BY_MODEL.get(payload.get("model"))
        reply = own.pop(0) if own else SCRIPT.pop(0) if SCRIPT else {"content": "ok"}
        if reply.get("status"):
            self.send_response(reply["status"])
            for k, v in (reply.get("headers") or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": reply.get("error", "nope")}}).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        for k, v in (reply.get("headers") or {}).items():
            self.send_header(k, v)
        self.end_headers()
        chunks = []
        if reply.get("content"):
            for i in range(0, len(reply["content"]), 7):
                chunks.append({"choices": [{"delta": {"content": reply["content"][i:i + 7]}}]})
        for i, call in enumerate(reply.get("tool_calls") or []):
            args = call["raw"] if "raw" in call else json.dumps(call["arguments"])  # "raw": arguments exactly as a sloppy model writes them
            chunks.append({"choices": [{"delta": {"tool_calls": [{"index": i, "id": "call_%d" % i, "function": {"name": call["name"], "arguments": args}}]}}]})
        chunks.append({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        for c in chunks:
            self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


def start(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/v1" % srv.server_address[1]
