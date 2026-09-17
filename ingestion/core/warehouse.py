"""Entrepôt DuckDB : schéma, écriture idempotente, état d'ingestion.

Un seul fichier porte les données brutes et l'avancement de leur collecte. Les
deux s'écrivent dans la même transaction : sans cela, « reprendre où l'on s'est
arrêté » ne serait qu'une intention.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import duckdb

from ingestion.core.state import IngestionState, RunReport, SourceKey
from ingestion.core.table import TableSpec

logger = logging.getLogger(__name__)

RAW_SCHEMA = "raw"
META_SCHEMA = "meta"

STATE_TABLE = f"{META_SCHEMA}.ingestion_state"
RUNS_TABLE = f"{META_SCHEMA}.ingestion_runs"

_META_DDL = (
    f"CREATE SCHEMA IF NOT EXISTS {META_SCHEMA}",
    f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}",
    f"""CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
        source VARCHAR NOT NULL,
        discipline VARCHAR NOT NULL,
        resource VARCHAR NOT NULL,
        high_watermark VARCHAR,
        backfill_cursor VARCHAR,
        records_seen BIGINT NOT NULL DEFAULT 0,
        last_run_at TIMESTAMPTZ,
        PRIMARY KEY (source, discipline, resource)
    )""",
    f"""CREATE TABLE IF NOT EXISTS {RUNS_TABLE} (
        run_id VARCHAR NOT NULL,
        source VARCHAR NOT NULL,
        discipline VARCHAR NOT NULL,
        resource VARCHAR NOT NULL,
        mode VARCHAR NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ NOT NULL,
        pages INTEGER NOT NULL,
        records INTEGER NOT NULL,
        inserted INTEGER NOT NULL,
        updated INTEGER NOT NULL,
        stop_reason VARCHAR NOT NULL,
        high_watermark VARCHAR,
        backfill_cursor VARCHAR,
        PRIMARY KEY (run_id)
    )""",
)


@dataclass(frozen=True, slots=True)
class UpsertResult:
    """Ce qu'une écriture a réellement changé."""

    received: int
    """Lignes présentées à l'écriture."""

    written: int
    """Lignes écrites après dédoublonnage interne au lot."""

    inserted: int
    """Lignes qui n'existaient pas."""

    updated: int
    """Lignes déjà connues, rafraîchies."""

    @property
    def duplicates_in_batch(self) -> int:
        return self.received - self.written


STATE_KEY_COLUMNS = ["source", "discipline", "resource"]


class OutdatedWarehouseError(RuntimeError):
    """L'entrepôt ouvert a été créé par une version antérieure du schéma."""


class Warehouse:
    """Accès à l'entrepôt. À ouvrir par `open()` ou `in_memory()`."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._connection = connection
        for statement in _META_DDL:
            self._connection.execute(statement)
        self._verify_state_schema()

    def _verify_state_schema(self) -> None:
        """Refuse un entrepôt dont la table d'état date d'avant l'ajout de la discipline.

        `CREATE TABLE IF NOT EXISTS` laisserait l'ancienne table en place, et
        l'écriture d'état échouerait plus tard sur un message obscur. Mieux vaut
        le dire ici : cette table se reconstruit seule, la supprimer ne coûte
        que de reprendre la collecte là où les données brutes s'arrêtent.
        """
        row = self._connection.execute(
            "SELECT constraint_column_names FROM duckdb_constraints() "
            "WHERE database_name = current_database() AND schema_name = ? "
            "AND table_name = ? AND constraint_type = 'PRIMARY KEY'",
            [META_SCHEMA, STATE_TABLE.split(".")[-1]],
        ).fetchone()
        if row is not None and list(row[0]) != STATE_KEY_COLUMNS:
            raise OutdatedWarehouseError(
                f"{STATE_TABLE} a pour clé {list(row[0])} au lieu de {STATE_KEY_COLUMNS} : "
                f"entrepôt antérieur à l'ajout de la discipline dans l'identité d'un flux. "
                f"Supprimer cette table (DROP TABLE {STATE_TABLE}) suffit — elle se "
                f"reconstruit à la prochaine collecte."
            )

    @classmethod
    def open(cls, path: Path) -> Warehouse:
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("entrepôt : %s", path)
        return cls(duckdb.connect(str(path)))

    @classmethod
    def in_memory(cls) -> Warehouse:
        """Entrepôt éphémère, pour les tests."""
        return cls(duckdb.connect(":memory:"))

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Regroupe données et état dans une même unité : tout ou rien."""
        self._connection.begin()
        try:
            yield
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    # -- Lecture ------------------------------------------------------------

    def query(self, sql: str, parameters: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
        return self._connection.execute(sql, list(parameters or [])).fetchall()

    def scalar(self, sql: str, parameters: Sequence[Any] | None = None) -> Any:
        row = self._connection.execute(sql, list(parameters or [])).fetchone()
        return None if row is None else row[0]

    def table_exists(self, qualified_name: str) -> bool:
        schema, _, name = qualified_name.rpartition(".")
        count = self.scalar(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ?",
            [schema, name],
        )
        return bool(count)

    def count(self, qualified_name: str) -> int:
        return int(self.scalar(f"SELECT count(*) FROM {qualified_name}") or 0)

    # -- Écriture -----------------------------------------------------------

    def ensure_table(self, spec: TableSpec) -> None:
        self._connection.execute(spec.create_schema_sql())
        self._connection.execute(spec.create_table_sql())

    def upsert(self, spec: TableSpec, rows: Sequence[Mapping[str, Any]]) -> UpsertResult:
        """Écrit un lot sans jamais créer de doublon.

        DuckDB refuse de mettre à jour deux fois la même ligne dans un seul
        `INSERT` : un lot contenant deux fois la même clé est donc réduit avant
        écriture, la dernière version l'emportant.
        """
        if not rows:
            return UpsertResult(received=0, written=0, inserted=0, updated=0)

        unique: dict[tuple[Any, ...], Mapping[str, Any]] = {}
        for row in rows:
            unique[spec.key_of(row)] = row
        if len(unique) != len(rows):
            logger.debug(
                "%s : %d doublon(s) dans le lot, réduits avant écriture",
                spec.qualified_name,
                len(rows) - len(unique),
            )

        before = self.count(spec.qualified_name)
        self._connection.executemany(
            spec.upsert_sql(), [spec.row_to_tuple(row) for row in unique.values()]
        )
        inserted = self.count(spec.qualified_name) - before
        return UpsertResult(
            received=len(rows),
            written=len(unique),
            inserted=inserted,
            updated=len(unique) - inserted,
        )

    # -- État ---------------------------------------------------------------

    def load_state(self, key: SourceKey) -> IngestionState | None:
        row = self._connection.execute(
            f"SELECT high_watermark, backfill_cursor, records_seen, last_run_at "
            f"FROM {STATE_TABLE} WHERE source = ? AND discipline = ? AND resource = ?",
            [key.name, key.discipline, key.resource],
        ).fetchone()
        if row is None:
            return None
        return IngestionState(
            key=key,
            high_watermark=row[0],
            backfill_cursor=row[1],
            records_seen=int(row[2]),
            last_run_at=row[3],
        )

    def save_state(self, state: IngestionState) -> None:
        self._connection.execute(
            f"""INSERT INTO {STATE_TABLE}
                (source, discipline, resource, high_watermark, backfill_cursor,
                 records_seen, last_run_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, discipline, resource) DO UPDATE SET
                high_watermark = EXCLUDED.high_watermark,
                backfill_cursor = EXCLUDED.backfill_cursor,
                records_seen = EXCLUDED.records_seen,
                last_run_at = EXCLUDED.last_run_at""",
            [
                state.key.name,
                state.key.discipline,
                state.key.resource,
                state.high_watermark,
                state.backfill_cursor,
                state.records_seen,
                state.last_run_at,
            ],
        )

    def all_states(self) -> list[IngestionState]:
        rows = self.query(
            f"SELECT source, discipline, resource, high_watermark, backfill_cursor, "
            f"records_seen, last_run_at FROM {STATE_TABLE} ORDER BY source, resource"
        )
        return [
            IngestionState(
                key=SourceKey(name=row[0], discipline=row[1], resource=row[2]),
                high_watermark=row[3],
                backfill_cursor=row[4],
                records_seen=int(row[5]),
                last_run_at=row[6],
            )
            for row in rows
        ]

    def record_run(self, report: RunReport) -> str:
        """Conserve la trace d'une exécution, réussie ou non."""
        run_id = uuid.uuid4().hex
        self._connection.execute(
            f"""INSERT INTO {RUNS_TABLE}
                (run_id, source, discipline, resource, mode, started_at, finished_at, pages,
                 records, inserted, updated, stop_reason, high_watermark, backfill_cursor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                run_id,
                report.key.name,
                report.key.discipline,
                report.key.resource,
                str(report.mode),
                report.started_at,
                report.finished_at,
                report.pages,
                report.records,
                report.inserted,
                report.updated,
                str(report.stop_reason),
                report.state_after.high_watermark,
                report.state_after.backfill_cursor,
            ],
        )
        return run_id
