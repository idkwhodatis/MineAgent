"""Strict pinned native checkpoint projection, not publication/receipt authority."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

PROGRAM_FIELDS = frozenset(
    [
        "id",
        "code",
        "changes_description",
        "language",
        "parent_id",
        "generation",
        "timestamp",
        "iteration_found",
        "metrics",
        "complexity",
        "diversity",
        "metadata",
        "prompts",
        "artifacts_json",
        "artifact_dir",
        "embedding",
    ]
)
METADATA_FIELDS = frozenset(
    [
        "island_feature_maps",
        "islands",
        "archive",
        "best_program_id",
        "island_best_programs",
        "last_iteration",
        "current_island",
        "island_generations",
        "last_migration_generation",
        "feature_stats",
    ]
)
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PROGRAMS = 64
MAX_STATE_BYTES = 786432


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("Noncanonical native program identity")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate native JSON key")
        result[key] = value
    return result


def _json(raw):
    return json.loads(
        raw,
        object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
    )


def canonical(state):
    return json.dumps(
        state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def state_sha256(state):
    return hashlib.sha256(canonical(state)).hexdigest()


def _normalize(metadata, programs):
    if type(metadata) is not dict or set(metadata) != METADATA_FIELDS:
        raise ValueError("Unsupported native metadata schema")
    if type(programs) is not dict or not 1 <= len(programs) <= MAX_PROGRAMS:
        raise ValueError("Invalid native population size")
    metadata = _json(canonical(metadata))
    programs = _json(canonical(programs))
    k = metadata["last_iteration"]
    if type(k) is not int or k < 0:
        raise ValueError("Invalid native frontier")
    for pid, program in programs.items():
        _uuid(pid)
        if (
            type(program) is not dict
            or set(program) != PROGRAM_FIELDS
            or program["id"] != pid
        ):
            raise ValueError("Unsupported native program schema")
        code = program["code"]
        if (
            type(code) is not str
            or not code
            or code.startswith("\ufeff")
            or "\r" in code
            or "\0" in code
        ):
            raise ValueError("Invalid native source grammar")
        if program["prompts"] is not None or program["artifact_dir"] is not None:
            raise ValueError("External artifacts/prompts forbidden")
        if program["parent_id"] is not None:
            _uuid(
                program["parent_id"]
            )  # External ancestry is verified by host closure.
        if (
            type(program["iteration_found"]) is not int
            or not 0 <= program["iteration_found"] <= k
        ):
            raise ValueError("Program beyond checkpoint frontier")
        if (
            type(program["metrics"]) is not dict
            or type(program["metadata"]) is not dict
        ):
            raise ValueError("Invalid native program fields")
        if program["language"] != "python" or (
            program["changes_description"] is not None
            and type(program["changes_description"]) is not str
        ):
            raise ValueError("Unsupported native language/description")
        if type(program["generation"]) is not int or program["generation"] < 0:
            raise ValueError("Invalid program generation")
        if any(
            type(program[name]) not in (int, float)
            for name in ("timestamp", "complexity", "diversity")
        ):
            raise ValueError("Invalid numeric program field")
        if program["embedding"] is not None and (
            type(program["embedding"]) is not list
            or any(type(x) not in (int, float) for x in program["embedding"])
        ):
            raise ValueError("Invalid program embedding")
        if program["artifacts_json"] is not None and (
            type(program["artifacts_json"]) is not str
            or type(_json(program["artifacts_json"])) is not dict
        ):
            raise ValueError("Invalid inline artifacts")
    # Initial capability is deliberately one island. No reconstruction repair.
    if type(metadata["islands"]) is not list or len(metadata["islands"]) != 1:
        raise ValueError("Unsupported island count")
    for key in ("island_feature_maps", "island_best_programs", "island_generations"):
        if type(metadata[key]) is not list or len(metadata[key]) != 1:
            raise ValueError("Inconsistent island metadata")
    if type(metadata["current_island"]) is not int or metadata["current_island"] != 0:
        raise ValueError("Invalid current island")
    for number in metadata["island_generations"] + [
        metadata["last_migration_generation"]
    ]:
        if type(number) is not int or number < 0:
            raise ValueError("Invalid native generation")
    for ids in metadata["islands"] + [metadata["archive"]]:
        if (
            type(ids) is not list
            or any(type(i) is not str for i in ids)
            or len(ids) != len(set(ids))
            or not set(ids) <= programs.keys()
        ):
            raise ValueError("Invalid native population references")
        ids.sort()
    if not metadata["islands"][0]:
        raise ValueError("Empty island would trigger native redistribution")
    refs = [metadata["best_program_id"], *metadata["island_best_programs"]]
    for mapping in metadata["island_feature_maps"]:
        if type(mapping) is not dict:
            raise ValueError("Invalid native feature map")
        refs.extend(mapping.values())
    if any(
        ref is not None and (type(ref) is not str or ref not in programs)
        for ref in refs
    ):
        raise ValueError("Dangling native population reference")
    if (
        metadata["best_program_id"] is None
        or type(metadata["feature_stats"]) is not dict
    ):
        raise ValueError("Missing best or invalid feature stats")
    state = {"metadata": metadata, "programs": programs}
    if len(canonical(state)) > MAX_STATE_BYTES:
        raise ValueError("Native state exceeds bound")
    return state


def snapshot(database):
    """Project precisely the native fields saved by the pinned ProgramDatabase."""
    metadata = {
        name: getattr(database, name)
        for name in METADATA_FIELDS
        if name != "feature_stats"
    }
    metadata["islands"] = [sorted(ids) for ids in database.islands]
    metadata["archive"] = sorted(database.archive)
    metadata["feature_stats"] = database._serialize_feature_stats()
    return _normalize(
        metadata, {pid: p.to_dict() for pid, p in database.programs.items()}
    )


def validate_directory(path):
    """Reject native leniency before load; host separately checks exact signed inventory."""
    root = Path(path)
    if (
        root.is_symlink()
        or not root.is_dir()
        or any(p.is_symlink() for p in root.parents)
    ):
        raise ValueError("Unsafe native directory")
    entries = list(root.iterdir())
    names = {p.name for p in entries}
    if not {"metadata.json", "programs"} <= names or len(names) != 4:
        raise ValueError("Unexpected native inventory")
    best_sources = names - {"metadata.json", "programs", "best_program_info.json"}
    if len(best_sources) != 1 or next(iter(best_sources)) != "best_program.py":
        raise ValueError("Invalid best source inventory")
    programs_dir = root / "programs"
    if programs_dir.is_symlink() or not programs_dir.is_dir():
        raise ValueError("Unsafe native programs directory")
    files = [p for p in entries if p != programs_dir] + list(programs_dir.iterdir())
    if len(files) > MAX_PROGRAMS + 3:
        raise ValueError("Native file count exceeded")
    raw = {}
    total = 0
    for file in files:
        if (
            file.is_symlink()
            or not file.is_file()
            or file.stat().st_size > MAX_FILE_BYTES
        ):
            raise ValueError("Unsafe native file")
        data = file.read_bytes()
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("Native file size exceeded")
        raw[file.relative_to(root).as_posix()] = data
    programs = {}
    for name, data in raw.items():
        if name.startswith("programs/"):
            file = Path(name)
            if file.suffix != ".json":
                raise ValueError("Unexpected program file")
            programs[_uuid(file.stem)] = _json(data)
    state = _normalize(_json(raw["metadata.json"]), programs)
    best = programs[state["metadata"]["best_program_id"]]
    if raw[next(iter(best_sources))] != best["code"].encode("utf-8"):
        raise ValueError("Best source mismatch")
    info = _json(raw["best_program_info.json"])
    expected = {
        "id": best["id"],
        "generation": best["generation"],
        "iteration": best["iteration_found"],
        "current_iteration": state["metadata"]["last_iteration"],
        "metrics": best["metrics"],
        "language": best["language"],
        "timestamp": best["timestamp"],
    }
    if (
        type(info) is not dict
        or set(info) != set(expected) | {"saved_at"}
        or any(info[k] != v for k, v in expected.items())
    ):
        raise ValueError("Best information mismatch")
    if type(info["saved_at"]) not in (int, float):
        raise ValueError("Invalid native save timestamp")
    return state
