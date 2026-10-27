"""Synthetic HTTP provider for the real026 entrypoint, not fixed broker dispatch.

The source is delivered through the same actual SDK response and host parser as
an operator provider. Only the HTTP server is a double; numeric work must execute.
"""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOURCE = """import json
from datetime import datetime, timezone
from pathlib import Path
import h5py
import numpy as np

root = Path("/input")
manifest = json.loads((root / "manifest.json").read_text())
observations = json.loads((root / "observations.json").read_text())
listings = json.loads((root / "listings.json").read_text())
field = [v["name"] for v in manifest["input_fields"]].index("close")
event = datetime.fromisoformat(manifest["output_event_time"])
event_ns = int((event - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()) * 1000000000
rows = []
for listing in listings:
    current = [v for v in observations if v["listing_code"] == listing["code"] and v["session"] == manifest["output_session"]]
    if len(current) != 1:
        raise ValueError("Exact current session required")
    rows.append((event_ns, listing["code"], float(current[0]["values"][field]), 0))
target = Path("/output/result.h5")
with h5py.File(target, "x") as output:
    group = output.create_group("factor")
    for index, (name, dtype) in enumerate((("event_ns", "<i8"), ("listing_code", "<u4"), ("value", "<f8"), ("state", "u1"))):
        group.create_dataset(name, data=np.array([row[index] for row in rows], dtype=dtype))
target.chmod(0o644)
"""
DERIVED_SOURCE = SOURCE.replace(
    'float(current[0]["values"][field])',
    '(float(current[0]["values"][field]) ** 2)',
)
GUIDANCE = "Research cross-sectional closing-price level, not momentum or returns."


@contextmanager
def provider_server(*, sources=(SOURCE,), port=0):
    captured = []
    chats = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < length <= 262144
                or self.headers.get("Authorization") != "Bearer synthetic-local-only"
            ):
                self.send_error(400)
                return
            request = json.loads(self.rfile.read(length))
            captured.append((self.path, request))
            if self.path == "/v1/chat/completions":
                ordinal = len(chats)
                chats.append(request)
                generation, stage = divmod(ordinal, 4)
                if generation >= len(sources):
                    self.send_error(400)
                    return
                contents = (
                    json.dumps({"code": sources[generation]}),
                    json.dumps(
                        {
                            "output_format_decision": True,
                            "output_format_feedback": "Inspect the actual numeric evaluation feedback.",
                        }
                    ),
                    "The generated code reads current close only; confirm actual retained result and declared objective.",
                    json.dumps(
                        {
                            "final_decision": True,
                            "final_feedback": "Use actual numeric and declared objective evidence.",
                        }
                    ),
                )
                result = {
                    "id": f"synthetic-chat-{ordinal}",
                    "object": "chat.completion",
                    "created": 1,
                    "model": request["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": contents[stage],
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 100,
                        "total_tokens": 200,
                    },
                }
            elif self.path == "/v1/embeddings":
                values = request["input"]
                values = [values] if isinstance(values, str) else values
                result = {
                    "object": "list",
                    "model": request["model"],
                    "data": [
                        {"object": "embedding", "index": index, "embedding": [1.0, 0.0]}
                        for index in range(len(values))
                    ],
                    "usage": {"prompt_tokens": 100, "total_tokens": 100},
                }
            else:
                self.send_error(404)
                return
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", captured
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
