"""Protected analysis journal and bounded result inode reads, no Qlib import."""

import hashlib
import os
import stat

from packages.research_runtime.analysis_process_journal import (
    AnalysisExtraction,
    AnalysisIdentity,
    AnalysisLaunch,
    AnalysisStop,
    validate_analysis_journal,
)
from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.numeric_journal import read_numeric_record
from runtimes.research_supervisor.supervisor import _directory, operation_path

MAX_OUTPUT = 1_048_576


def read_analysis_journal(root, admission, *, require_output=False):
    path = operation_path(root, admission)
    launch = AnalysisLaunch.model_validate_json(
        canonical(read_numeric_record(path / "control" / "launch.json"))
    )
    identity = AnalysisIdentity.model_validate_json(
        canonical(read_numeric_record(path / "control" / "identity.json"))
    )
    stop = AnalysisStop.model_validate_json(
        canonical(read_numeric_record(path / "control" / "stop.json"))
    )
    extraction = None
    if require_output:
        extraction = AnalysisExtraction.model_validate(
            read_numeric_record(path / "control" / "extraction.json")
        )
    if launch.intent.admission != admission:
        raise ProcessError("analysis_process_journal_identity")
    fd = _directory(root)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (launch.root_device, launch.root_inode):
            raise ProcessError("analysis_process_root_identity")
    finally:
        os.close(fd)
    return validate_analysis_journal(launch, identity, stop, extraction)


def read_opaque_output(root, admission, extraction):
    """Not an authority API: service must resolve DB/physical receipts first."""
    extraction = AnalysisExtraction.model_validate(extraction)
    return _read_output(
        root, admission, extraction.basename, extraction.size_bytes, extraction.sha256
    )


def _read_output(root, admission, basename, size_bytes, sha256):
    if basename not in ("analysis.json",) or not 0 < size_bytes <= MAX_OUTPUT:
        raise ProcessError("analysis_process_output_identity")
    directory = _directory(operation_path(root, admission) / "output")
    fd = None
    try:
        fd = os.open(
            basename,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory,
        )
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != size_bytes
            or before.st_size > MAX_OUTPUT
        ):
            raise ProcessError("analysis_process_output_identity")
        chunks, remaining = [], size_bytes + 1
        while remaining:
            part = os.read(fd, min(65536, remaining))
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        value = b"".join(chunks)
        after = os.fstat(fd)
        if (
            len(value) != size_bytes
            or hashlib.sha256(value).hexdigest() != sha256
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            raise ProcessError("analysis_process_output_identity")
        return value
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def validate_analysis_quiescence(operation, record):
    """Cold physical check only; restored root inode is not a new launch grant."""
    launch = AnalysisLaunch.model_validate_json(
        canonical(read_numeric_record(operation / "control" / "launch.json"))
    )
    if (str(launch.intent.admission.owner_id), str(launch.intent.admission.id)) != (
        operation.parent.name,
        operation.name,
    ):
        raise ProcessError("analysis_process_journal_identity")
    identity = AnalysisIdentity.model_validate_json(
        canonical(read_numeric_record(operation / "control" / "identity.json"))
    )
    stop = AnalysisStop.model_validate_json(
        canonical(read_numeric_record(operation / "control" / "stop.json"))
    )
    validate_analysis_journal(launch, identity, stop)
