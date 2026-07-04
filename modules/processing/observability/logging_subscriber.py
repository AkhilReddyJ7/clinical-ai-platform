"""Logging observability subscriber (event-backbone structural refactor).

Turns Event objects into log lines via shared.logging.logger. Depends
only on that logger and the Event model — no event-dispatch logic lives
here (that's modules.processing.events), and this module never calls
subscribe() itself (that's modules.processing.observability.registry,
the only wiring point).
"""

from modules.processing.events import Event
from shared.logging.logger import logger


def _logging_subscriber(event: Event) -> None:
    """Logs are a projection of the event, not a parallel thing execution
    code does itself — worker.py/pipeline.py used to call logger.info(...)
    directly before the event backbone existed.
    """
    logger.info(
        "event: %s job_id=%s document_id=%s metadata=%s",
        event.event_type.value,
        event.job_id,
        event.document_id,
        event.metadata,
    )
