import hashlib
import io
import zipfile

import pytest

from packages.factors.package import FactorPackageError, decode_package, encode_package


def obj(content=b"original bytes\r\n"):
    return {"objects/" + hashlib.sha256(content).hexdigest() + ".bin": content}


def test_deterministic_and_exact_bytes():
    metadata = {"origin_namespace": "foreign", "history_completeness": "unknown"}
    first = encode_package(metadata, obj())
    assert first == encode_package(metadata, obj())
    parsed = decode_package(first)
    assert parsed.manifest == metadata and parsed.objects == obj()
    assert parsed.archive_sha256 == hashlib.sha256(first).hexdigest()


def test_wrong_content_address():
    with pytest.raises(FactorPackageError):
        encode_package({}, {"objects/" + "a" * 64 + ".bin": b"x"})


@pytest.mark.parametrize(
    "name", ["../evil", "/tmp/evil", "objects/../../evil", "manifest.json"]
)
def test_path_and_duplicate(name):
    source = encode_package({}, obj())
    stream = io.BytesIO(source)
    with zipfile.ZipFile(stream, "a") as z:
        z.writestr(name, b"x")
    with pytest.raises(FactorPackageError):
        decode_package(stream.getvalue())


def test_compressed_archive_rejected():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", b"{}")
    with pytest.raises(FactorPackageError):
        decode_package(b.getvalue())


def test_unknown_manifest_and_size():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("manifest.json", b'{"x":1,"x":2}')
    with pytest.raises(FactorPackageError):
        decode_package(b.getvalue())
    with pytest.raises(FactorPackageError):
        decode_package(b"x" * 10_000_001)


def test_hidden_trailing_or_prefixed_bytes_rejected():
    raw = encode_package({}, obj())
    for altered in [raw + b"hidden", b"prefix" + raw]:
        with pytest.raises(FactorPackageError):
            decode_package(altered)


def test_manifest_deep_payload_rejected():
    value = {}
    for _ in range(26):
        value = {"nested": value}
    with pytest.raises(FactorPackageError):
        encode_package(value, {})


def test_code_not_executed(tmp_path):
    code = b"raise RuntimeError('must never run')"
    name = "objects/" + hashlib.sha256(code).hexdigest() + ".py"
    assert decode_package(encode_package({}, {name: code})).objects[name] == code
