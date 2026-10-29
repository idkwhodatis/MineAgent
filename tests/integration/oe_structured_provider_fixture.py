"""Local HTTP double only: real SDK carries its generated JSON, never a score."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from packages.factors.contracts import canonical_bytes


@contextmanager
def provider_server():
    captured = []
    reply = {"candidate": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if (
                self.path != "/v1/chat/completions"
                or not 0 < size <= 262144
                or reply["candidate"] is None
            ):
                self.send_error(400)
                return
            body = json.loads(self.rfile.read(size))
            captured.append(body)
            content = canonical_bytes(reply["candidate"]).decode("ascii")
            payload = {
                "id": "local-oe-" + str(len(captured)),
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 128,
                    "completion_tokens": 64,
                    "total_tokens": 192,
                },
            }
            raw = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port) + "/v1", captured, reply
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise AssertionError("Local provider did not stop")
