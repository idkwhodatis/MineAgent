"""Side-effect-free selection for the only enabled engine family."""

from packages.research_runtime.identity import RunIdentityError, require_legacy_identity


def require_seed_identity(run) -> None:
    if require_legacy_identity(run) is not None:
        raise RunIdentityError("Seed staging requires the immutable seed format")
