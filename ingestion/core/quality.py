"""Tests de données.

Les tests de code disent que la collecte fait ce qu'on a écrit ; ceux-ci disent
que ce qui est arrivé dans l'entrepôt se tient. Une source déclare ses attentes
à côté de sa table, et elles sont vérifiables à tout moment — sur le jeu d'essai
comme sur l'entrepôt de production.

Une attente se formule en SQL et compte des lignes fautives : zéro faute, elle
passe. Les seuils viennent de ce que les données montrent réellement, pas d'une
intuition — d'où la distinction entre ce qui est faux (`ERROR`) et ce qui est
seulement incomplet (`WARN`), comme une équipe non identifiée chez OpenDota.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ingestion.core.warehouse import Warehouse

logger = logging.getLogger(__name__)


class Severity(StrEnum):
    """Ce qu'une faute vaut."""

    ERROR = "error"
    """Une donnée fausse : l'entrepôt ne devrait pas la contenir."""

    WARN = "warn"
    """Une donnée incomplète, mais que la source livre ainsi."""


@dataclass(frozen=True, slots=True)
class Check:
    """Une attente sur une table, exprimée en SQL.

    `sql` doit rendre un entier unique : le nombre de lignes en faute.
    """

    name: str
    description: str
    table: str
    sql: str
    severity: Severity = Severity.ERROR

    @classmethod
    def rows_where(
        cls,
        name: str,
        *,
        description: str,
        table: str,
        violation: str,
        severity: Severity = Severity.ERROR,
    ) -> Check:
        """Attente formulée par sa faute : `violation` décrit la ligne fautive."""
        return cls(
            name=name,
            description=description,
            table=table,
            sql=f"SELECT count(*) FROM {table} WHERE {violation}",
            severity=severity,
        )

    @classmethod
    def unique(
        cls,
        name: str,
        *,
        description: str,
        table: str,
        column: str,
        severity: Severity = Severity.ERROR,
    ) -> Check:
        """Attente d'unicité : compte les valeurs présentes plus d'une fois."""
        return cls(
            name=name,
            description=description,
            table=table,
            sql=(
                f"SELECT count(*) FROM ("
                f"SELECT {column} FROM {table} GROUP BY {column} HAVING count(*) > 1)"
            ),
            severity=severity,
        )


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Ce qu'une attente a donné. `violations` à `None` : table absente."""

    check: Check
    violations: int | None

    @property
    def skipped(self) -> bool:
        return self.violations is None

    @property
    def passed(self) -> bool:
        return self.violations == 0

    @property
    def failed(self) -> bool:
        return not self.skipped and not self.passed

    @property
    def is_blocking(self) -> bool:
        return self.failed and self.check.severity is Severity.ERROR

    def summary(self) -> str:
        if self.skipped:
            return f"[passe] {self.check.name} : table {self.check.table} absente"
        if self.passed:
            return f"[ok]    {self.check.name}"
        marque = "[ECHEC]" if self.check.severity is Severity.ERROR else "[avert.]"
        return (
            f"{marque} {self.check.name} : {self.violations} ligne(s) "
            f"— {self.check.description}"
        )


def run_checks(warehouse: Warehouse, checks: Iterable[Check]) -> list[CheckResult]:
    """Évalue des attentes. Une table jamais collectée n'est pas une faute."""
    results = []
    for check in checks:
        if not warehouse.table_exists(check.table):
            logger.debug("%s : table %s absente, attente ignorée", check.name, check.table)
            results.append(CheckResult(check=check, violations=None))
            continue
        violations = int(warehouse.scalar(check.sql) or 0)
        results.append(CheckResult(check=check, violations=violations))
    return results


def has_blocking_failure(results: Sequence[CheckResult]) -> bool:
    return any(result.is_blocking for result in results)
