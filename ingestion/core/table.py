"""Déclaration des tables.

Une source décrit la table qu'elle alimente ; le noyau en déduit le DDL et
l'écriture idempotente. Personne n'écrit de SQL d'insertion à la main.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Column:
    """Une colonne, et le type DuckDB qui la porte."""

    name: str
    sql_type: str


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Une table du schéma brut, avec sa clé d'unicité.

    La clé est une colonne, ou plusieurs : un identifiant de page Liquipedia
    n'est unique qu'au sein de son wiki, et c'est le couple qui désigne une
    ligne. `write_once` liste les colonnes figées à la première écriture : la
    date de première ingestion ne doit pas être réécrite quand une ligne est
    revue.
    """

    schema: str
    name: str
    columns: tuple[Column, ...]
    primary_key: str | tuple[str, ...]
    write_once: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        names = self.column_names
        if len(set(names)) != len(names):
            raise ValueError(f"{self.qualified_name} : colonnes en double")
        if not self.key_columns:
            raise ValueError(f"{self.qualified_name} : clé vide")
        missing = [key for key in self.key_columns if key not in names]
        if missing:
            raise ValueError(f"{self.qualified_name} : clé « {missing[0]} » absente")
        unknown = self.write_once - set(names)
        if unknown:
            raise ValueError(f"{self.qualified_name} : colonnes inconnues {sorted(unknown)}")

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def key_columns(self) -> tuple[str, ...]:
        """La clé, toujours sous forme de séquence — simple ou composite."""
        if isinstance(self.primary_key, str):
            return (self.primary_key,)
        return tuple(self.primary_key)

    @property
    def updatable_columns(self) -> tuple[str, ...]:
        """Colonnes réécrites quand une ligne déjà connue est revue."""
        frozen = self.write_once | set(self.key_columns)
        return tuple(name for name in self.column_names if name not in frozen)

    def key_of(self, row: Mapping[str, Any]) -> tuple[Any, ...]:
        """Valeur de clé d'une ligne, de quoi reconnaître deux fois la même."""
        return tuple(row[name] for name in self.key_columns)

    def create_schema_sql(self) -> str:
        return f"CREATE SCHEMA IF NOT EXISTS {self.schema}"

    def create_table_sql(self) -> str:
        body = ",\n    ".join(f"{column.name} {column.sql_type}" for column in self.columns)
        return (
            f"CREATE TABLE IF NOT EXISTS {self.qualified_name} (\n"
            f"    {body},\n"
            f"    PRIMARY KEY ({', '.join(self.key_columns)})\n"
            f")"
        )

    def upsert_sql(self) -> str:
        """Écriture idempotente : relancer une ingestion ne duplique rien.

        Sans colonne réinscriptible, une ligne déjà connue est laissée intacte.
        """
        columns = ", ".join(self.column_names)
        placeholders = ", ".join("?" for _ in self.columns)
        conflict = ", ".join(self.key_columns)
        head = f"INSERT INTO {self.qualified_name} ({columns}) VALUES ({placeholders})"
        updatable = self.updatable_columns
        if not updatable:
            return f"{head}\nON CONFLICT ({conflict}) DO NOTHING"
        assignments = ", ".join(f"{name} = EXCLUDED.{name}" for name in updatable)
        return f"{head}\nON CONFLICT ({conflict}) DO UPDATE SET {assignments}"

    def row_to_tuple(self, row: Mapping[str, Any]) -> tuple[Any, ...]:
        """Ordonne une ligne selon la table, en refusant tout écart de schéma.

        Une clé manquante ou inattendue est une faute de normalisation : mieux
        vaut l'arrêter ici que la découvrir plus tard dans les données.
        """
        expected = set(self.column_names)
        missing = sorted(expected - row.keys())
        unexpected = sorted(row.keys() - expected)
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"colonnes manquantes {missing}")
            if unexpected:
                details.append(f"colonnes inattendues {unexpected}")
            raise ValueError(
                f"{self.qualified_name} : ligne non conforme — {', '.join(details)}"
            )
        return tuple(row[name] for name in self.column_names)
