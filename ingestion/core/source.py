"""Le contrat que remplit toute source.

C'est la seule chose que le noyau connaisse d'un fournisseur. Ajouter une
discipline ou un fournisseur revient à écrire une classe ici — ni le lanceur,
ni l'entrepôt, ni la ligne de commande n'en savent quoi que ce soit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from types import TracebackType
from typing import Any

from ingestion.core.quality import Check
from ingestion.core.state import IngestionState, Mode, SourceKey, Walk, utcnow
from ingestion.core.table import TableSpec


class SourceError(RuntimeError):
    """La source a renvoyé quelque chose que son contrat n'autorise pas."""


@dataclass(frozen=True, slots=True)
class Page:
    """Une page de résultats bruts, telle que le fournisseur l'a rendue.

    `next_cursor` à `None` signifie qu'il n'y a rien en dessous.
    """

    records: tuple[Mapping[str, Any], ...]
    next_cursor: str | None


class Source(ABC):
    """Interface commune à toutes les sources.

    Cinq gestes suffisent à décrire un flux : aller chercher une page, la
    normaliser, savoir par où commencer, mesurer ce que la descente a couvert,
    et en déduire le nouvel état.
    """

    def __init__(
        self, *, key: SourceKey, table: TableSpec, checks: Sequence[Check] = ()
    ) -> None:
        self.key = key
        self.table = table
        self.checks = tuple(checks)
        """Attentes de la source sur sa propre table : elle seule sait ce qu'elle livre."""

    @abstractmethod
    def fetch(self, cursor: str | None) -> Page:
        """Récupère une page. `cursor` à `None` demande le début du flux."""

    @abstractmethod
    def normalize(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Traduit un enregistrement brut en une ligne conforme à `table`."""

    @abstractmethod
    def start_cursor(self, mode: Mode, state: IngestionState) -> str | None:
        """Par où commencer, selon le mode et ce qui est déjà connu."""

    @abstractmethod
    def extend(
        self, walk: Walk, rows: Sequence[Mapping[str, Any]], before: IngestionState
    ) -> Walk:
        """Intègre une page à la descente en cours.

        `before` est l'état d'avant l'exécution : c'est à lui que se compare
        chaque page pour savoir si la descente a rejoint du connu.
        """

    @abstractmethod
    def advance(self, *, before: IngestionState, walk: Walk, mode: Mode) -> IngestionState:
        """Déduit le nouvel état de l'état initial et de la descente accomplie."""

    def close(self) -> None:  # noqa: B027  (crochet facultatif, pas une obligation)
        """Libère ce qui doit l'être. Une source sans ressource n'a rien à faire ici."""

    def __enter__(self) -> Source:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class DescendingIdSource(Source):
    """Source paginée par identifiant décroissant.

    C'est le cas d'OpenDota — « donne-moi ce qui est strictement sous cet
    identifiant » — et de tout fournisseur exposant un curseur comparable. La
    gestion de l'état y est entièrement générique : seules `fetch` et
    `normalize` restent à écrire.

    Invariant maintenu : `[backfill_cursor, high_watermark]` reste un intervalle
    parcouru de façon contiguë.
    """

    def __init__(
        self,
        *,
        key: SourceKey,
        table: TableSpec,
        id_column: str,
        checks: Sequence[Check] = (),
    ) -> None:
        super().__init__(key=key, table=table, checks=checks)
        if id_column not in table.column_names:
            raise ValueError(f"{table.qualified_name} : colonne « {id_column} » absente")
        self.id_column = id_column

    def start_cursor(self, mode: Mode, state: IngestionState) -> str | None:
        """Le rattrapage repart du sommet ; le backfill reprend sous la frontière."""
        if mode is Mode.BACKFILL:
            return state.backfill_cursor
        return None

    def extend(
        self, walk: Walk, rows: Sequence[Mapping[str, Any]], before: IngestionState
    ) -> Walk:
        if not rows:
            return walk
        identifiers = [int(row[self.id_column]) for row in rows]
        lowest = min(identifiers)
        highest = max(identifiers)
        reached_known = walk.reached_known or (
            before.high_watermark is not None and lowest <= int(before.high_watermark)
        )
        return Walk(
            lowest=str(lowest if walk.lowest is None else min(lowest, int(walk.lowest))),
            highest=str(highest if walk.highest is None else max(highest, int(walk.highest))),
            records=walk.records + len(rows),
            reached_known=reached_known,
        )

    def advance(self, *, before: IngestionState, walk: Walk, mode: Mode) -> IngestionState:
        if walk.lowest is None or walk.highest is None:
            return before
        lowest = int(walk.lowest)
        highest = int(walk.highest)
        known_high = None if before.high_watermark is None else int(before.high_watermark)
        known_low = None if before.backfill_cursor is None else int(before.backfill_cursor)

        if known_low is None:
            frontier = lowest
        elif mode is Mode.BACKFILL or walk.reached_known:
            # La descente prolonge l'intervalle déjà parcouru : les deux se rejoignent.
            frontier = min(lowest, known_low)
        else:
            # Rattrapage encore au-dessus du connu : cette descente forme un îlot,
            # et la zone sautée sera reprise par un backfill.
            frontier = lowest

        return replace(
            before,
            high_watermark=str(highest if known_high is None else max(highest, known_high)),
            backfill_cursor=str(frontier),
            records_seen=before.records_seen + walk.records,
            last_run_at=utcnow(),
        )
