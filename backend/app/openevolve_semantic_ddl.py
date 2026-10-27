"""Read-only fragment loader pinned to reviewed model shapes; never activates SQL."""

import hashlib
from pathlib import Path

MODEL_SHA256 = {
    "openevolve_models.py": "dceb65a43c6a61ad0e82a6dd7ac7fddad4f1e126c0a52c8729bfbc1e14932993",
    "openevolve_event_models.py": "136b9f2acaf303d72f04d824a2904230cedad5876ea052b56a6a326752a5cede",
    "openevolve_worker_models.py": "0b0e74d223fa1645b8f26c82b4335a7fa61169ad96e02c1c3e09a43094309445",
}


def fragment():
    root = Path(__file__).resolve().parent
    for name, expected in MODEL_SHA256.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("OE semantic SQL model snapshot drift")
    return (root / "sql" / "openevolve_initial_semantics.sql").read_text()
