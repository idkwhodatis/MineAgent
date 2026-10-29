"""Queue registration without importing an engine or executing strategy code."""

import procrastinate

from backend.app.core.config import Settings, get_settings

TASK_NAME = "tradegrad.execute_run"


def create_queue_app(settings: Settings | None = None, database_url: str | None = None):
    if database_url is None:
        settings = settings or get_settings()
        database_url = settings.psycopg_url
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=database_url)
    )

    @app.task(
        name=TASK_NAME,
        queue="research",
        retry=procrastinate.RetryStrategy(max_attempts=3, wait=5),
    )
    def execute_run(run_id: str, generation: int):
        from workers.research.executor import execute

        execute(run_id, generation, settings=settings)

    from workers.research.rdagent_queue import TASK_NAME as RD_TASK_NAME

    @app.task(
        name=RD_TASK_NAME,
        queue="research",
        retry=procrastinate.RetryStrategy(max_attempts=3, wait=5),
    )
    def execute_rd_run(run_id: str, generation: int):
        from workers.research.rdagent_queue import execute

        execute(run_id, generation, settings=settings or get_settings())

    from workers.research.oe_structured_queue import TASK_NAME as OE_TASK_NAME

    @app.task(
        name=OE_TASK_NAME,
        queue="research",
        retry=procrastinate.RetryStrategy(max_attempts=3, wait=5),
    )
    def execute_oe_structured_run(run_id: str, generation: int):
        from workers.research.oe_structured_queue import execute

        # Redelivery can only claim an untouched queued run, never restart an
        # already-created attempt or an uncertain physical unit.
        execute(run_id, generation, settings=settings or get_settings())

    return app
