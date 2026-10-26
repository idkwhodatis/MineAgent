"""Disabled, reviewable process proposals, NOT process admission or launch authority.

No proposal is registered with the supervisor. Image pins are supplied explicitly by
trusted installation review, never read from inherited environment. Activation needs
separate migration, containment, kernel IPC and resource acceptance. In particular,
hostile candidate isolation is not established by describing a fixed entrypoint.
"""

from dataclasses import asdict, dataclass
from types import MappingProxyType

from packages.research_runtime.budget_contracts import MAX_UNITS, BudgetVector
from packages.research_runtime.process_contracts import canonical, digest, sha256

SCHEMA = "mineagent.process-manifest-proposal.v2"
MIB = 1024**2
JOURNAL_BYTES = 65536


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_UNITS:
        raise ValueError("Invalid process proposal integer")


@dataclass(frozen=True)
class InputBinding:
    """Only a fixed container destination, never a caller-provided host path."""

    destination: str
    sha256: str
    size_bytes: int

    def __post_init__(self):
        if type(self.destination) is not str:
            raise ValueError("Invalid input destination")
        sha256(self.sha256)
        _integer(self.size_bytes, 1)


@dataclass(frozen=True)
class Resources:
    memory_bytes: int
    pids: int
    wall_milliseconds: int
    cpu_nanoseconds_per_second: int
    tmpfs: tuple[tuple[str, int], ...]
    shm_bytes: int
    inodes_per_tmpfs: int
    nofile: int
    file_bytes: int
    extraction_bytes: int
    publication_copy_bytes: int
    journal_bytes: int = JOURNAL_BYTES

    def __post_init__(self):
        for key, value in self.__dict__.items():
            if key != "tmpfs":
                _integer(value)
        if type(self.tmpfs) is not tuple:
            raise ValueError("Mutable resource policy")
        for item in self.tmpfs:
            if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
                raise ValueError("Invalid tmpfs policy")
            _integer(item[1], 1)


@dataclass(frozen=True)
class IPCPolicy:
    mode: str
    mount: str | None
    purposes: tuple[str, ...]
    peer_scope: str


@dataclass(frozen=True)
class IsolationPolicy:
    pid_namespace: str = "private"
    ipc_namespace: str = "private"
    user_policy: str = "nonroot_host_uid_gid"
    no_new_privileges: bool = True
    privileged: bool = False
    devices: tuple[str, ...] = ()
    cap_drop: tuple[str, ...] = ("ALL",)
    log_driver: str = "none"
    core_bytes: int = 0
    tmpfs_options: tuple[str, ...] = ("noexec", "nosuid", "nodev")
    input_host_path_policy: str = "host_derived_pinned_nofollow"
    deadline_policy: str = "not_accepted_no_launch"


ISOLATION = IsolationPolicy()


@dataclass(frozen=True)
class ProposalProfile:
    role: str
    executable: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    input_limits: tuple[tuple[str, int], ...]
    resources: Resources
    ipc: IPCPolicy
    output_limits: tuple[tuple[str, int], ...]
    unresolved: tuple[str, ...]


ENV = (
    ("PYTHONDONTWRITEBYTECODE", "1"),
    ("PYTHONUNBUFFERED", "1"),
    ("PYTHONUTF8", "1"),
    ("LC_ALL", "C.UTF-8"),
    ("OMP_NUM_THREADS", "1"),
    ("OPENBLAS_NUM_THREADS", "1"),
    ("MKL_NUM_THREADS", "1"),
)
NO_IPC = IPCPolicy("none", None, (), "none")
ORCHESTRATOR_IPC = IPCPolicy(
    "host_issued_context",
    "/ipc",
    ("model", "evaluation"),
    "kernel_unit_and_current_fence_and_assigned_phase_context",
)
COMMON_PENDING = (
    "runtime_image_and_environment_acceptance",
    "real_resource_measurement",
    "independent_deadline_acceptance",
    "versioned_database_and_backup_integration",
)


def _resources(memory, pids, seconds, work, tmp, output=0, extract=0):
    mounts = tuple(
        (path, size)
        for path, size in (("/work", work), ("/tmp", tmp), ("/output", output))
        if size
    )
    return Resources(
        memory,
        pids,
        seconds * 1000,
        1_000_000_000,
        mounts,
        65536,
        1024,
        64,
        16 * MIB,
        extract,
        extract,
    )


PROPOSALS = MappingProxyType(
    {
        "rd_orchestrator.proposal.v1": ProposalProfile(
            "rd_orchestrator",
            ("python", "-I", "/app/runtimes/rdagent/entrypoint.py"),
            ENV,
            (("/input/bootstrap.json", MIB),),
            _resources(1024 * MIB, 32, 300, 64 * MIB, 4 * MIB, extract=16 * MIB),
            ORCHESTRATOR_IPC,
            (),
            COMMON_PENDING
            + ("factor_input_inventory_unresolved", "scoped_ipc_acceptance"),
        ),
        "openevolve_orchestrator.proposal.v1": ProposalProfile(
            "openevolve_orchestrator",
            ("python", "-I", "/app/runtimes/openevolve/entrypoint.py"),
            ENV,
            (
                ("/bootstrap/bootstrap.json", 256 * 1024),
                ("/bootstrap/seed.py", 128 * 1024),
                ("/bootstrap/source-manifest.json", 64 * 1024),
            ),
            _resources(1024 * MIB, 64, 600, 64 * MIB, 16 * MIB, extract=8 * MIB),
            ORCHESTRATOR_IPC,
            (),
            COMMON_PENDING
            + ("checkpoint_inventory_unresolved", "scoped_ipc_acceptance"),
        ),
        "factor_cutoff_candidate.proposal.v1": ProposalProfile(
            "factor_cutoff_candidate",
            ("python", "-I", "/app/runtimes/factor_execution/entrypoint.py"),
            ENV,
            (
                ("/input/source.py", 128 * 1024),
                ("/input/observations.json", 16 * MIB),
                ("/input/listings.json", MIB),
                ("/input/manifest.json", MIB),
            ),
            _resources(256 * MIB, 16, 10, 0, 4 * MIB, 16 * MIB, 16 * MIB),
            NO_IPC,
            (("/output/result.h5", 16 * MIB),),
            COMMON_PENDING
            + ("hostile_child_cannot_disable_deadline", "stable_output_extraction"),
        ),
        "factor_hdf_normalizer.proposal.v1": ProposalProfile(
            "factor_hdf_normalizer",
            ("python", "-I", "/app/runtimes/factor_normalizer/entrypoint.py"),
            ENV + (("HDF5_PLUGIN_PATH", ""), ("HDF5_PLUGIN_PRELOAD", "::")),
            (
                ("/input/result.h5", 16 * MIB),
                ("/input/listings.json", MIB),
                ("/input/manifest.json", MIB),
            ),
            _resources(256 * MIB, 8, 10, 0, 4 * MIB, 16 * MIB, 16 * MIB),
            NO_IPC,
            (("/output/values.parquet", 16 * MIB),),
            COMMON_PENDING + ("hostile_parser_containment", "stable_output_extraction"),
        ),
    }
)


@dataclass(frozen=True)
class ImageConfigInventory:
    """Explicit reviewed image-inspect inventory, never inferred from a tag.

    These are image defaults, not host environment inheritance. The image digest
    binds the inventory; callers must obtain it from their trusted build review.
    """

    image_id: str
    environment: tuple[tuple[str, str], ...]
    working_directory: str

    def __post_init__(self):
        if type(self.image_id) is not str or not self.image_id.startswith("sha256:"):
            raise ValueError("Unpinned image inventory")
        sha256(self.image_id[7:])
        allowed = {"PATH", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256"}
        allowed.update(key for key, _ in ENV)
        allowed.update({"HDF5_PLUGIN_PATH", "HDF5_PLUGIN_PRELOAD"})
        if type(self.environment) is not tuple:
            raise ValueError("Mutable image environment inventory")
        names = []
        for pair in self.environment:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or type(pair[0]) is not str
                or type(pair[1]) is not str
                or pair[0] not in allowed
                or len(pair[1]) > 1024
                or "\x00" in pair[1]
                or "\n" in pair[1]
            ):
                raise ValueError("Unknown image environment inventory")
            names.append(pair[0])
        if names != sorted(set(names)) or "PATH" not in names:
            raise ValueError("Incomplete or duplicate image environment")
        if self.working_directory not in ("", "/opt/mineagent"):
            raise ValueError("Unknown image working directory")


@dataclass(frozen=True)
class ProcessManifestV2:
    schema_version: str
    admission_status: str
    profile: str
    role: str
    image_id: str
    executable: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    resources: Resources
    ipc: IPCPolicy
    inputs: tuple[InputBinding, ...]
    output_limits: tuple[tuple[str, int], ...]
    unresolved: tuple[str, ...]
    network: str = "none"
    root_readonly: bool = True
    capabilities: tuple[str, ...] = ()
    input_readonly: bool = True
    environment_inheritance: bool = False
    isolation: IsolationPolicy = ISOLATION
    image_config: ImageConfigInventory | None = None

    def __post_init__(self):
        spec = PROPOSALS.get(self.profile) if type(self.profile) is str else None
        if (
            spec is None
            or self.schema_version != SCHEMA
            or self.admission_status != "disabled"
        ):
            raise ValueError("Unknown or activated process proposal")
        for field in (
            "role",
            "executable",
            "environment",
            "resources",
            "ipc",
            "output_limits",
            "unresolved",
        ):
            if getattr(self, field) != getattr(spec, field):
                raise ValueError("Process proposal profile drift")
        if (
            type(self.resources) is not Resources
            or type(self.ipc) is not IPCPolicy
            or type(self.isolation) is not IsolationPolicy
            or canonical(asdict(self.isolation)) != canonical(asdict(ISOLATION))
        ):
            raise ValueError("Invalid isolation policy")
        if (
            self.network != "none"
            or self.root_readonly is not True
            or self.input_readonly is not True
            or self.environment_inheritance is not False
            or type(self.capabilities) is not tuple
            or self.capabilities
        ):
            raise ValueError("Unsafe process proposal")
        if type(self.image_id) is not str or not self.image_id.startswith("sha256:"):
            raise ValueError("Unpinned process proposal image")
        sha256(self.image_id[7:])
        if self.image_config is not None and (
            type(self.image_config) is not ImageConfigInventory
            or self.image_config.image_id != self.image_id
        ):
            raise ValueError("Image inventory does not match manifest")
        if type(self.inputs) is not tuple or any(
            type(item) is not InputBinding for item in self.inputs
        ):
            raise ValueError("Invalid process input inventory")
        limits = dict(spec.input_limits)
        paths = tuple(item.destination for item in self.inputs)
        if paths != tuple(sorted(limits)):
            raise ValueError("Missing, duplicate or substituted process input")
        if any(item.size_bytes > limits[item.destination] for item in self.inputs):
            raise ValueError("Oversized process input")

    def public(self):
        # Convert tuples to canonical JSON arrays without retaining mutable state.
        import json

        return json.loads(canonical(asdict(self)))

    @property
    def sha256(self):
        return digest(self.public())

    @property
    def input_sha256(self):
        return digest([asdict(item) for item in self.inputs])

    @property
    def minimum_reservation(self):
        r = self.resources
        # Two simultaneous input copies: staged preparation and sealed mount.
        # Output tmpfs, extraction and durable publication copies overlap too.
        storage = (
            2 * sum(item.size_bytes for item in self.inputs)
            + sum(size for _, size in r.tmpfs)
            + r.shm_bytes
            + r.journal_bytes
            + r.extraction_bytes
            + r.publication_copy_bytes
        )
        return BudgetVector(
            process_slots=1,
            wall_milliseconds=r.wall_milliseconds,
            storage_bytes=storage,
        )

    def require_launch_authority(self):
        raise ValueError("Disabled process proposal is never launch authority")


def propose_manifest(
    profile, image_id, inputs, *, trusted_image_pins, reviewed_image_config=None
):
    """Explicit review-time pins; this function grants no execution capability."""
    if (
        type(trusted_image_pins) is not dict
        or trusted_image_pins.get(profile) != image_id
    ):
        raise ValueError("Image does not match reviewed proposal pin")
    spec = PROPOSALS.get(profile)
    if spec is None:
        raise ValueError("Unknown process proposal")
    return ProcessManifestV2(
        SCHEMA,
        "disabled",
        profile,
        spec.role,
        image_id,
        spec.executable,
        spec.environment,
        spec.resources,
        spec.ipc,
        tuple(sorted(inputs, key=lambda item: item.destination)),
        spec.output_limits,
        spec.unresolved,
        image_config=reviewed_image_config,
    )


def validate_manifest(value, *, trusted_image_pins, trusted_image_configs=None):
    """Decode the exact JSON proposal grammar; never upgrades historical v1."""
    import json
    from dataclasses import fields

    if type(value) is not dict or set(value) != {
        f.name for f in fields(ProcessManifestV2)
    }:
        raise ValueError("Invalid process proposal fields")
    try:
        if value["profile"] not in PROPOSALS:
            raise ValueError("Unknown profile")
        inputs = tuple(InputBinding(**item) for item in value["inputs"])
        manifest = propose_manifest(
            value["profile"],
            value["image_id"],
            inputs,
            trusted_image_pins=trusted_image_pins,
            reviewed_image_config=(trusted_image_configs or {}).get(value["image_id"]),
        )
        # Canonical byte equality catches unknown nested fields, bool/int aliases,
        # unsorted inputs and any supplied environment/resource/security drift.
        encoded = canonical(value)
        if len(encoded) > 16384 or encoded != canonical(manifest.public()):
            raise ValueError("Process proposal drift")
        # Require JSON-native trees, not custom objects with equality overrides.
        if json.loads(encoded) != value:
            raise ValueError("Non-JSON process proposal")
        return manifest
    except (KeyError, TypeError) as exc:
        raise ValueError("Invalid process proposal") from exc
