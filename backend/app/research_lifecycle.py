"""Control-plane adapter for pure immutable run identity checks."""

from packages.research_runtime.identity import RunIdentityError, require_legacy_identity


def require_legacy_run(run) -> str | None:
    try:
        return require_legacy_identity(run)
    except RunIdentityError:
        # Lazy import keeps services -> lifecycle -> pure contracts acyclic.
        from backend.app.services import DomainError

        raise DomainError(
            "unsupported_run_identity",
            "Run purpose, engine or immutable configuration is unsupported",
            409,
        ) from None
