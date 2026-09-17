"""État d'ingestion : ce qu'on sait avoir collecté, et ce qu'une exécution a fait.

Le curseur est une chaîne opaque, jamais interprétée par le noyau. C'est la
source qui sait s'il s'agit d'un identifiant décroissant ou d'un jeton de
continuation — condition pour qu'OpenDota et Liquipedia partagent ce module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum


class Mode(StrEnum):
    """Sens de parcours d'une exécution."""

    CATCH_UP = "catchup"
    """Repartir du sommet et descendre jusqu'à retrouver du connu."""

    BACKFILL = "backfill"
    """Reprendre sous la frontière basse pour creuser l'historique."""


class StopReason(StrEnum):
    """Pourquoi une exécution s'est arrêtée."""

    CAUGHT_UP = "caught_up"
    """La descente a rejoint des enregistrements connus : rien ne manque au-dessus."""

    EXHAUSTED = "exhausted"
    """La source n'a plus rien à donner en dessous."""

    PAGE_LIMIT = "page_limit"
    """Plafond de pages atteint ; la prochaine exécution reprendra ici."""

    EMPTY_PAGE = "empty_page"
    """Page vide renvoyée par la source."""

    FAILED = "failed"
    """Interrompue par une erreur ; l'état déjà enregistré reste valable."""


@dataclass(frozen=True, slots=True)
class SourceKey:
    """Identité d'un flux : un fournisseur, une discipline, une ressource."""

    name: str
    discipline: str
    resource: str

    def __str__(self) -> str:
        return f"{self.name}.{self.resource}"


@dataclass(frozen=True, slots=True)
class IngestionState:
    """Avancement persistant d'un flux.

    Invariant : l'intervalle `[backfill_cursor, high_watermark]` a été parcouru
    de façon contiguë. Tout ce qui tombe en dehors reste à collecter.
    """

    key: SourceKey
    high_watermark: str | None = None
    backfill_cursor: str | None = None
    records_seen: int = 0
    last_run_at: datetime | None = None

    @classmethod
    def empty(cls, key: SourceKey) -> IngestionState:
        return cls(key=key)

    @property
    def is_bootstrapped(self) -> bool:
        """Vrai dès qu'une exécution a écrit quelque chose."""
        return self.high_watermark is not None

    def touched(self, *, records: int, at: datetime) -> IngestionState:
        return replace(self, records_seen=self.records_seen + records, last_run_at=at)


@dataclass(frozen=True, slots=True)
class Walk:
    """Ce que la descente en cours a couvert, agrégé page après page.

    Distinguer la descente de l'état permet de comparer chaque page à l'état
    d'avant l'exécution, et non à un état déjà déplacé par les pages précédentes.
    """

    lowest: str | None = None
    highest: str | None = None
    records: int = 0
    reached_known: bool = False


@dataclass(frozen=True, slots=True)
class RunReport:
    """Compte rendu d'une exécution : journalisé, puis conservé dans l'entrepôt."""

    key: SourceKey
    mode: Mode
    started_at: datetime
    finished_at: datetime
    pages: int
    records: int
    inserted: int
    updated: int
    stop_reason: StopReason
    state_before: IngestionState
    state_after: IngestionState

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def gap_suspected(self) -> bool:
        """Un rattrapage qui n'a pas rejoint le connu laisse un trou derrière lui.

        Rien n'est perdu — un backfill repassera dessous — mais l'intervalle
        contigu redémarre plus haut, et cela doit se dire.
        """
        return (
            self.mode is Mode.CATCH_UP
            and self.pages > 0
            and self.state_before.is_bootstrapped
            and self.stop_reason is not StopReason.CAUGHT_UP
        )

    def summary(self) -> str:
        return (
            f"{self.key} [{self.mode}] : {self.pages} page(s), "
            f"{self.records} enregistrement(s), {self.inserted} nouveau(x), "
            f"{self.updated} mis à jour, arrêt « {self.stop_reason} » "
            f"en {self.duration_seconds:.1f}s"
        )


def utcnow() -> datetime:
    """Horloge unique du noyau : toujours en UTC, remplaçable dans les tests."""
    return datetime.now(UTC)
