"""La boucle d'ingestion, commune à toutes les sources.

Elle ne sait rien d'un fournisseur : elle demande des pages, les écrit, avance
l'état, et s'arrête quand il n'y a plus de raison de continuer. Chaque page est
écrite avec l'état correspondant dans une seule transaction, si bien qu'une
interruption — quota, réseau, Ctrl-C — ne coûte au plus qu'une page.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ingestion.core.source import Source
from ingestion.core.state import (
    IngestionState,
    Mode,
    RunReport,
    StopReason,
    Walk,
    utcnow,
)
from ingestion.core.warehouse import Warehouse

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 10


def run(
    source: Source,
    warehouse: Warehouse,
    *,
    mode: Mode = Mode.CATCH_UP,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> RunReport:
    """Exécute une ingestion et rend compte de ce qu'elle a fait.

    `max_pages` borne la consommation de quota : une exécution trop courte ne
    perd rien, elle laisse simplement du travail à la suivante.
    """
    if max_pages < 1:
        raise ValueError("une exécution parcourt au moins une page")

    warehouse.ensure_table(source.table)
    before = warehouse.load_state(source.key) or IngestionState.empty(source.key)
    state = before
    walk = Walk()
    cursor = source.start_cursor(mode, before)
    started_at = utcnow()
    pages = inserted = updated = 0
    stop_reason = StopReason.PAGE_LIMIT

    logger.info(
        "%s [%s] : départ %s, plafond %d page(s)",
        source.key,
        mode,
        f"sous {cursor}" if cursor else "au sommet du flux",
        max_pages,
    )

    try:
        for page_number in range(1, max_pages + 1):
            page = source.fetch(cursor)
            if not page.records:
                stop_reason = StopReason.EMPTY_PAGE
                break

            rows = [source.normalize(record) for record in page.records]
            walk = source.extend(walk, rows, before)
            state = source.advance(before=before, walk=walk, mode=mode)

            with warehouse.transaction():
                result = warehouse.upsert(source.table, rows)
                warehouse.save_state(state)

            pages += 1
            inserted += result.inserted
            updated += result.updated
            logger.info(
                "%s : page %d — %d enregistrement(s), %d nouveau(x), frontière %s",
                source.key,
                page_number,
                result.received,
                result.inserted,
                state.backfill_cursor,
            )

            if mode is Mode.CATCH_UP and walk.reached_known:
                stop_reason = StopReason.CAUGHT_UP
                break
            if page.next_cursor is None:
                stop_reason = StopReason.EXHAUSTED
                break
            cursor = page.next_cursor
    except BaseException:
        # L'état des pages déjà écrites est acquis : on en garde la trace avant
        # de laisser l'erreur remonter.
        warehouse.record_run(
            _report(
                source=source,
                mode=mode,
                started_at=started_at,
                pages=pages,
                walk=walk,
                inserted=inserted,
                updated=updated,
                stop_reason=StopReason.FAILED,
                before=before,
                after=state,
            )
        )
        logger.exception("%s [%s] : interrompue après %d page(s)", source.key, mode, pages)
        raise

    report = _report(
        source=source,
        mode=mode,
        started_at=started_at,
        pages=pages,
        walk=walk,
        inserted=inserted,
        updated=updated,
        stop_reason=stop_reason,
        before=before,
        after=state,
    )
    warehouse.record_run(report)
    logger.info(report.summary())
    if report.gap_suspected:
        logger.warning(
            "%s : le rattrapage n'a pas rejoint %s — la zone sautée sera reprise "
            "par un backfill, ou évitée en relançant avec un plafond de pages plus haut",
            source.key,
            before.high_watermark,
        )
    return report


def _report(
    *,
    source: Source,
    mode: Mode,
    started_at: datetime,
    pages: int,
    walk: Walk,
    inserted: int,
    updated: int,
    stop_reason: StopReason,
    before: IngestionState,
    after: IngestionState,
) -> RunReport:
    return RunReport(
        key=source.key,
        mode=mode,
        started_at=started_at,
        finished_at=utcnow(),
        pages=pages,
        records=walk.records,
        inserted=inserted,
        updated=updated,
        stop_reason=stop_reason,
        state_before=before,
        state_after=after,
    )
