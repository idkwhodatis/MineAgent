"""Fresh first-generation request must consume the actual frozen steer/parent text."""

from sqlalchemy import select

from backend.app.rdagent_seed_models import FactorSeededSubmission
from backend.app.rdagent_seed_services import reread_saved_seed
from backend.app.research_lifecycle import require_neutral_transaction
from packages.rdagent_adapter.provider_prompt import first_provider_messages


def require_first_provider_prompt(session, fence, call, request, config, settings):
    """Only first generation/first member; later upstream continuations stay intact.

    The caller establishes that no member exists before invoking this guard. The
    checked request itself is retained as model_request before SDK admission; this
    is not a declaration that generated code obeyed the guidance.
    """
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    if call.phase != "generation" or call.ordinal != 0 or request.kind != "chat":
        raise ValueError("Exact initial generation request required")
    row = session.scalar(
        select(FactorSeededSubmission).filter_by(
            owner_id=fence.owner_id, run_id=fence.run_id
        )
    )
    seed = (
        None
        if row is None
        else reread_saved_seed(session, fence.owner_id, row, settings)
    )
    system, user = first_provider_messages(config, seed)
    if tuple((message.role, message.content) for message in request.messages) != (
        ("system", system),
        ("user", user),
    ):
        raise ValueError("First provider request does not consume exact frozen inputs")
