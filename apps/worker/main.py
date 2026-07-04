"""Worker process entrypoint (ADR-0021/ADR-0022).

Runs modules/processing/worker.py's polling loop as its own long-running
process, decoupled from the API. The loop itself (claim, dispatch,
retry/stale-job recovery) and everything it claims and executes
(modules/processing/repository.py, modules/processing/pipeline.py) were
already built and tested against a real Postgres queue -- this module is
only the piece ADR-0022 named as still missing when the route contract
was decided ("the worker... implementation work for the worker epic"):
something that actually starts the loop as a process, so a docker-compose
service has something to run.

Uses the same shared.database.session.AsyncSessionLocal the API process
uses (same Settings.database_url), matching worker.py's own
composition-root pattern: this process builds nothing app-specific, it
just starts modules/processing/worker.run_worker_loop and stops it
cleanly on SIGTERM/SIGINT (docker compose down's normal stop signal, or
Ctrl-C locally) rather than only on SIGKILL after a grace period.
"""

import asyncio
import signal

from modules.processing.worker import start_worker, stop_worker
from shared.config.settings import get_settings
from shared.database.session import AsyncSessionLocal
from shared.logging.logger import configure_logging, logger


async def main() -> None:
    configure_logging()
    settings = get_settings()
    logger.info(
        "Starting %s worker version=%s environment=%s",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )

    task = await start_worker(AsyncSessionLocal)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()
    logger.info("worker: received shutdown signal, stopping cleanly")
    await stop_worker(task)


if __name__ == "__main__":
    asyncio.run(main())
