"""Bulk backfill/reprocess CLI (ADR-0032).

Targets every currently-`validated` document (the only legal
force_reprocess_job precondition) or a single --document-id, and enqueues
a forced-reprocess job for each. Enqueueing is sufficient -- the existing
worker service (docker-compose.yml's `worker`) must already be running to
actually pick up and execute the resulting `queued` jobs; this script
does not run the pipeline itself.

Usage:
    uv run python -m scripts.run_backfill --note "..." --dry-run
    uv run python -m scripts.run_backfill --note "backfill: upgrade to claude-haiku-4-6, batch=2026-07-05" --yes
    uv run python -m scripts.run_backfill --document-id <uuid> --note "manual reprocess: bad OCR" --yes
"""

import argparse
import uuid
from datetime import datetime

from sqlalchemy import select

from modules.ingestion.models import Document, DocumentStatus
from modules.processing.repository import force_reprocess_job
from modules.processing.state_machine import IllegalTransitionError
from shared.database.session import AsyncSessionLocal


async def _candidate_document_ids(
    *, document_id: uuid.UUID | None, before: datetime | None, limit: int | None
) -> list[uuid.UUID]:
    async with AsyncSessionLocal() as db:
        if document_id is not None:
            return [document_id]

        stmt = select(Document.id).where(Document.status == DocumentStatus.VALIDATED)
        if before is not None:
            stmt = stmt.where(Document.created_at < before)
        stmt = stmt.order_by(Document.created_at.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await db.execute(stmt)).scalars().all())


async def _run_backfill(document_ids: list[uuid.UUID], *, note: str) -> tuple[int, int]:
    enqueued = 0
    skipped = 0
    async with AsyncSessionLocal() as db:
        for document_id in document_ids:
            try:
                job = await force_reprocess_job(db, document_id, trigger_note=note)
            except IllegalTransitionError:
                # Raced out from under the filter (e.g. someone else
                # reprocessed it, or its status changed) between the
                # candidate query and this call -- skip, don't abort.
                skipped += 1
                continue
            if job is None:
                skipped += 1
                continue
            enqueued += 1
            print(f"enqueued job={job.id} document={document_id} attempt={job.attempt_number}")
    return enqueued, skipped


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--document-id", type=uuid.UUID, default=None)
    parser.add_argument("--before", type=datetime.fromisoformat, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--note", required=True, help="justification recorded on every job's trigger_note"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    document_ids = await _candidate_document_ids(
        document_id=args.document_id, before=args.before, limit=args.limit
    )
    print(f"{len(document_ids)} candidate document(s) for reprocessing")

    if args.dry_run:
        for document_id in document_ids:
            print(f"  would reprocess: {document_id}")
        return 0

    if not document_ids:
        return 0

    if not args.yes:
        confirmation = input(f"Reprocess {len(document_ids)} document(s)? [y/N] ")
        if confirmation.strip().lower() != "y":
            print("aborted")
            return 1

    enqueued, skipped = await _run_backfill(document_ids, note=args.note)
    print(f"enqueued={enqueued} skipped={skipped}")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
