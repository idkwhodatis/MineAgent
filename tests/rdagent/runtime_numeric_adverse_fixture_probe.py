"""Whitelisted local constructors only; NOT Docker or arbitrary-source acceptance.

Run with the existing installed numeric environment using Python -I. Does not
execute generated source.py or invoke the protected wrappers on the host.
"""

import importlib.util
import json
import tempfile
from pathlib import Path

from runtimes.factor_normalizer.normalize import normalize_file

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "maintained_adverse_fixture", ROOT / "runtimes/factor_execution/adverse_fixture.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
EXPECTED = {
    "malformed": "file signature not found",
    "external_link": "dataset link",
    "soft_link": "dataset link",
    "vds": "unsupported dataset storage/type",
    "external_storage": "unsupported dataset storage/type",
    "gzip": "unsupported dataset storage/type",
    "shuffle": "unsupported dataset storage/type",
    "vlen": "unsupported dataset storage/type",
    "reference": "unsupported dataset storage/type",
    "compound": "unsupported dataset storage/type",
    "hardlink_alias": "unexpected factor grammar",
}


def parse(path):
    import hashlib

    return normalize_file(
        path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        event_ns=0,
        listing_codes=(1,),
        max_rows=3,
        max_bytes=16 * 1024 * 1024,
    )


def main():
    with tempfile.TemporaryDirectory(prefix="known-numeric-adverse-") as directory:
        folder = Path(directory)
        (folder / "manifest.json").write_text(
            json.dumps({"output_event_time": "1970-01-01T00:00:00+00:00"})
        )
        (folder / "listings.json").write_text(
            json.dumps([{"code": 1, "listing_id": "known"}])
        )
        # This constructor's underlying primitives are the shared valid control;
        # file mode is not an isolated access-control claim in this local probe.
        control = folder / "valid.h5"
        fixture.create_case("unreadable", control, folder)
        control.chmod(0o644)
        assert parse(control) == ((0, 1, 1.0, 0),)
        verified = []
        for name, message in EXPECTED.items():
            path = folder / (name + ".h5")
            fixture.create_case(name, path, folder)
            try:
                parse(path)
            except (ValueError, OSError) as error:
                if message not in str(error):
                    raise AssertionError(
                        "maintained case failed for wrong reason: " + name
                    ) from error
            else:
                raise AssertionError("maintained grammar violation accepted: " + name)
            verified.append(name)
        print(
            json.dumps(
                {
                    "scope": "known_local_fixture_constructors_only",
                    "docker_executed": False,
                    "valid_control_rows": 1,
                    "verified_cases": verified,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
