"""Real bounded no-follow process inventory plus explicit SQL quota doubles."""

import os
from contextlib import nullcontext
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy.sql.elements import TextClause

from backend.app import openevolve_process_quota as s
from backend.app.rdagent_provider_models import RDProviderTransportReceipt
from packages.research_runtime.budget_contracts import BudgetVector


def tree(tmp_path):
    owner = uuid4()
    directory = tmp_path / "research-processes" / str(owner) / str(uuid4())
    directory.mkdir(parents=True)
    (directory / "output.bin").write_bytes(b"actual retained output")
    return owner, directory


def test_actual_unknown_retained_bytes_are_charged(tmp_path):
    owner, _directory = tree(tmp_path)
    rows = s.process_bytes(tmp_path, 10)
    assert len(rows) == 3
    assert {o for o, _ in rows} == {owner}
    assert sum(size for _, size in rows) == len(b"actual retained output")


@pytest.mark.parametrize(
    "fault", ["symlink", "hardlink", "fifo", "foreign_dir", "root_link"]
)
def test_unaccountable_process_entries_fail_closed(tmp_path, fault):
    _owner, directory = tree(tmp_path)
    if fault == "symlink":
        (directory / "bad").symlink_to(directory / "output.bin")
    if fault == "hardlink":
        os.link(directory / "output.bin", directory / "bad")
    if fault == "fifo":
        os.mkfifo(directory / "bad")
    if fault == "foreign_dir":
        (tmp_path / "research-processes" / "not-a-uuid").mkdir()
    if fault == "root_link":
        (tmp_path / "research-processes").rename(tmp_path / "actual")
        (tmp_path / "research-processes").symlink_to(tmp_path / "actual")
    with pytest.raises((ValueError, OSError)):
        s.process_bytes(tmp_path, 10)


def test_directory_and_file_caps_are_both_counted(tmp_path):
    tree(tmp_path)
    with pytest.raises(ValueError, match="count"):
        s.process_bytes(tmp_path, 2)


def quota(monkeypatch, tmp_path, *, owner_limit=1000, global_limit=2000, files=10000):
    fence = NS(owner_id=uuid4())
    rows = []

    class Session:
        def execute(self, query):
            if query.column_descriptions[-1]["entity"] is RDProviderTransportReceipt:
                # This quota fixture has no SDK transports; do not suppress the
                # production installation-wide provider hold query.
                return NS(all=list)
            values = []
            for row in rows:
                row.id = getattr(row, "id", uuid4())
                row.state = getattr(row, "state", "reserved")
                values.append((row, None, None, None, None))
            return NS(all=lambda: values)

        def scalars(self, query):
            if isinstance(query, TextClause):
                assert str(query) == "SELECT version_num FROM alembic_version"
                return NS(all=lambda: ["026_rd_seeded_submissions"])
            model = query.column_descriptions[0]["entity"]
            return NS(all=lambda: rows if model is s.ResearchBudgetReservation else [])

    monkeypatch.setattr(s, "require_neutral_transaction", lambda *a, **kw: None)
    monkeypatch.setattr(s, "object_storage_guard", lambda *a: nullcontext())
    monkeypatch.setattr(s, "physical_inventory", lambda *a, **kw: ())
    from backend.app import openevolve_upload_quota as upload

    monkeypatch.setattr(upload, "upload_holds", lambda *a: ())
    settings = NS(
        storage_root=tmp_path,
        neutral_object_owner_max_bytes=owner_limit,
        neutral_object_installation_max_bytes=global_limit,
        neutral_object_max_files=files,
    )
    return fence, Session(), rows, settings


def test_settled_without_positive_stop_keeps_installation_charge(monkeypatch, tmp_path):
    fence, session, rows, settings = quota(monkeypatch, tmp_path)
    rows.append(
        NS(
            owner_id=fence.owner_id,
            state="settled",
            amounts=BudgetVector(storage_bytes=900).model_dump(),
        )
    )
    s.require_process_capacity(
        session, fence, BudgetVector(storage_bytes=100), settings, extra_files=528
    )
    with pytest.raises(ValueError, match="exhausted"):
        s.require_process_capacity(
            session, fence, BudgetVector(storage_bytes=101), settings, extra_files=528
        )


def test_foreign_owner_still_consumes_global_ceiling(monkeypatch, tmp_path):
    fence, session, rows, settings = quota(monkeypatch, tmp_path, global_limit=1000)
    rows.append(
        NS(owner_id=uuid4(), amounts=BudgetVector(storage_bytes=950).model_dump())
    )
    with pytest.raises(ValueError, match="exhausted"):
        s.require_process_capacity(
            session, fence, BudgetVector(storage_bytes=51), settings, extra_files=528
        )


def test_pending_file_floor_precedes_first_byte(monkeypatch, tmp_path):
    fence, session, rows, settings = quota(monkeypatch, tmp_path, files=1055)
    rows.append(
        NS(owner_id=fence.owner_id, amounts=BudgetVector(storage_bytes=1).model_dump())
    )
    with pytest.raises(ValueError, match="exhausted"):
        s.require_process_capacity(
            session, fence, BudgetVector(storage_bytes=1), settings, extra_files=528
        )


@pytest.mark.parametrize("value", [True, 0, -1, 1025, 1.0])
def test_new_file_floor_strict(monkeypatch, tmp_path, value):
    fence, session, _rows, settings = quota(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="ceiling"):
        s.require_process_capacity(
            session, fence, BudgetVector(storage_bytes=1), settings, extra_files=value
        )


@pytest.mark.parametrize(
    "fault",
    [None, "no_stop", "no_settlement", "not_stopped", "not_settled", "foreign_scope"],
)
def test_peak_release_only_after_exact_positive_stop_and_settlement(fault):
    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    reservation = NS(
        id=uuid4(),
        **scope,
        state="settled",
        amounts=BudgetVector(storage_bytes=900).model_dump(),
    )
    admission = NS(id=uuid4(), **scope)
    state = NS(admission_id=admission.id, **scope, status="stopped")
    stop = NS(admission_id=admission.id, **scope, kind="stop")
    settlement = NS(
        reservation_id=reservation.id,
        **scope,
        outcome="conservative_unknown",
        charge=reservation.amounts,
    )
    if fault == "no_stop":
        stop = None
    if fault == "no_settlement":
        settlement = None
    if fault == "not_stopped":
        state.status = "uncertain"
    if fault == "not_settled":
        reservation.state = "uncertain"
    if fault == "foreign_scope":
        stop.owner_id = uuid4()
    session = NS(
        execute=lambda q: NS(
            all=lambda: [(reservation, admission, state, stop, settlement)]
        )
    )
    if fault == "foreign_scope":
        with pytest.raises(ValueError, match="scope"):
            s.process_holds(session)
    else:
        result = s.process_holds(session)
        assert result == (() if fault is None else ((scope["owner_id"], 900, 528),))


@pytest.mark.parametrize(
    "revision,required",
    [("021_execution_snapshots", False), ("024_openevolve_initial", True)],
)
def test_explicit_capability_not_revision_order(revision, required):
    session = NS(scalars=lambda q: NS(all=lambda: [revision]))
    assert s.process_quota_required(session) is required


def test_024_missing_settings_rejected_before_any_capacity_effect(monkeypatch):
    session = NS(scalars=lambda q: NS(all=lambda: ["024_openevolve_initial"]))
    monkeypatch.setattr(
        s,
        "require_process_capacity",
        lambda *a, **k: pytest.fail("not before settings"),
    )
    with pytest.raises(ValueError, match="Explicit installation Settings"):
        s.admit_process_capacity(session, None, BudgetVector(process_slots=1), None)


def test_historical_capability_retains_settings_optional(monkeypatch):
    session = NS(scalars=lambda q: NS(all=lambda: ["021_execution_snapshots"]))
    monkeypatch.setattr(
        s, "require_process_capacity", lambda *a, **k: pytest.fail("historical path")
    )
    s.admit_process_capacity(session, None, BudgetVector(process_slots=1), None)


@pytest.mark.parametrize("name", ["peer.sock", "events.sock", "broker.sock"])
def test_fixed_socket_inode_is_counted_without_read_or_connect(
    tmp_path, monkeypatch, name
):
    """Actual directories/O_PATH FD; socket type is an explicit syscall double."""
    import stat

    owner, directory = tree(tmp_path)
    ipc = directory / "ipc"
    ipc.mkdir()
    target = ipc / name
    target.write_bytes(b"")
    real_stat, real_fstat, real_open = os.stat, os.fstat, os.open
    inode = target.stat().st_ino
    flags_seen = []

    def socket_info(info):
        if info.st_ino != inode:
            return info
        return NS(
            **{
                key: (stat.S_IFSOCK | 0o666) if key == "st_mode" else getattr(info, key)
                for key in (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_nlink",
                    "st_uid",
                    "st_gid",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
            }
        )

    def opened(path, flags, *args, **kwargs):
        if path == name:
            flags_seen.append(flags)
            assert flags == os.O_PATH | os.O_NOFOLLOW
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "stat", lambda *a, **kw: socket_info(real_stat(*a, **kw)))
    monkeypatch.setattr(os, "fstat", lambda fd: socket_info(real_fstat(fd)))
    monkeypatch.setattr(os, "open", opened)
    values = s.process_bytes(tmp_path, 10)
    assert flags_seen == [os.O_PATH | os.O_NOFOLLOW]
    assert len(values) == 5 and {entry[0] for entry in values} == {owner}
    assert sum(size for _, size in values) == len(b"actual retained output")
    with pytest.raises(ValueError, match="file count"):
        s.process_bytes(tmp_path, 4)


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_name",
        "wrong_directory",
        "wrong_admission",
        "hardlink",
        "foreign_uid",
        "replacement",
        "symlink_swap",
    ],
)
def test_socket_allowlist_and_inode_races_fail_closed(tmp_path, monkeypatch, fault):
    """Socket metadata double only; replacements/hardlinks/directories are real."""
    import stat

    _owner, directory = tree(tmp_path)
    if fault == "wrong_admission":
        replacement = directory.with_name("not-an-admission")
        directory.rename(replacement)
        directory = replacement
    ipc = directory / ("output" if fault == "wrong_directory" else "ipc")
    ipc.mkdir()
    name = "unknown.sock" if fault == "wrong_name" else "peer.sock"
    target = ipc / name
    target.write_bytes(b"")
    if fault == "hardlink":
        os.link(target, tmp_path / "extra-link")
    real_stat, real_fstat, real_open = os.stat, os.fstat, os.open
    inode = target.stat().st_ino

    def socket_info(info):
        if info.st_ino != inode:
            return info
        values = {
            key: getattr(info, key)
            for key in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_nlink",
                "st_uid",
                "st_gid",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        values["st_mode"] = stat.S_IFSOCK | 0o666
        if fault == "foreign_uid":
            values["st_uid"] += 1
        return NS(**values)

    def opened(path, flags, *args, **kwargs):
        if path == name and flags & os.O_PATH:
            if fault == "symlink_swap":
                target.unlink()
                target.symlink_to(directory / "output.bin")
            fd = real_open(path, flags, *args, **kwargs)
            if fault == "replacement":
                target.unlink()
                target.write_bytes(b"")
            return fd
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "stat", lambda *a, **kw: socket_info(real_stat(*a, **kw)))
    monkeypatch.setattr(os, "fstat", lambda fd: socket_info(real_fstat(fd)))
    monkeypatch.setattr(os, "open", opened)
    with pytest.raises((ValueError, OSError)):
        s.process_bytes(tmp_path, 20)
