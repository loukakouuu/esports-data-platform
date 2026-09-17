"""OpenDota — matchs professionnels Dota 2.

`GET /api/proMatches` rend 100 matchs par page, triés par identifiant
décroissant, et accepte `less_than_match_id` pour descendre plus bas. Ce
curseur strictement décroissant se prête exactement à une ingestion
incrémentale : la frontière basse suffit à reprendre le travail.

L'appel fonctionne sans clé ; une clé, si elle est fournie, relève le quota.
La cadence est tenue par le client HTTP, pas ici.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ingestion.core.config import Settings
from ingestion.core.http import HttpClient
from ingestion.core.source import DescendingIdSource, Page, SourceError
from ingestion.core.state import SourceKey, utcnow
from ingestion.core.table import Column, TableSpec

logger = logging.getLogger(__name__)

API_ROOT = "https://api.opendota.com/api"
PAGE_SIZE = 100
"""Taille de page imposée par l'API : une page plus courte signale la fin du flux."""

MIN_INTERVAL = 1.05
"""60 requêtes par minute autorisées ; on se tient juste en dessous."""

KEY = SourceKey(name="opendota", discipline="dota2", resource="pro_matches")

TABLE = TableSpec(
    schema="raw",
    name="opendota_pro_matches",
    columns=(
        Column("match_id", "BIGINT NOT NULL"),
        Column("start_time", "TIMESTAMPTZ"),
        Column("duration_seconds", "INTEGER"),
        Column("radiant_team_id", "BIGINT"),
        Column("radiant_name", "VARCHAR"),
        Column("dire_team_id", "BIGINT"),
        Column("dire_name", "VARCHAR"),
        Column("radiant_score", "INTEGER"),
        Column("dire_score", "INTEGER"),
        Column("radiant_win", "BOOLEAN"),
        Column("league_id", "BIGINT"),
        Column("league_name", "VARCHAR"),
        Column("series_id", "BIGINT"),
        Column("series_type", "INTEGER"),
        Column("parse_version", "INTEGER"),
        # La charge utile complète est conservée : le jour où l'API ajoute un
        # champ, l'historique ne sera pas à recollecter.
        Column("payload", "JSON NOT NULL"),
        Column("ingested_at", "TIMESTAMPTZ NOT NULL"),
        Column("refreshed_at", "TIMESTAMPTZ NOT NULL"),
    ),
    primary_key="match_id",
    write_once=frozenset({"ingested_at"}),
)


def _as_int(value: Any) -> int | None:
    """Convertit sans jamais deviner : ce qui n'est pas un entier vaut inconnu."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_timestamp(value: Any) -> datetime | None:
    """Traduit l'heure epoch d'OpenDota en instant UTC."""
    seconds = _as_int(value)
    if seconds is None or seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC)


class OpenDotaProMatches(DescendingIdSource):
    """Flux des matchs professionnels, du plus récent au plus ancien."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: HttpClient | None = None,
        page_size: int = PAGE_SIZE,
    ) -> None:
        super().__init__(key=KEY, table=TABLE, id_column="match_id")
        resolved = settings or Settings.from_env()
        self._api_key = resolved.opendota_api_key
        self._page_size = page_size
        self._client = client or HttpClient(
            user_agent=resolved.user_agent, min_interval=MIN_INTERVAL
        )

    def fetch(self, cursor: str | None) -> Page:
        params: dict[str, Any] = {}
        if cursor is not None:
            params["less_than_match_id"] = cursor
        if self._api_key:
            params["api_key"] = self._api_key

        payload = self._client.get_json(f"{API_ROOT}/proMatches", params)
        if not isinstance(payload, list):
            raise SourceError(f"proMatches : liste attendue, {type(payload).__name__} reçu")

        records = tuple(
            record
            for record in payload
            if isinstance(record, dict) and _as_int(record.get("match_id")) is not None
        )
        skipped = len(payload) - len(records)
        if skipped:
            logger.warning("proMatches : %d enregistrement(s) sans match_id ignoré(s)", skipped)
        if not records:
            return Page(records=(), next_cursor=None)

        lowest = min(int(record["match_id"]) for record in records)
        if cursor is not None and lowest >= int(cursor):
            # L'API a ignoré le curseur : continuer boucherait sur la même page.
            raise SourceError(
                f"proMatches : la page sous {cursor} redescend à {lowest}, "
                "le flux n'avance plus"
            )

        # Une page incomplète marque le bas du flux : rien à demander en dessous.
        next_cursor = str(lowest) if len(records) >= self._page_size else None
        return Page(records=records, next_cursor=next_cursor)

    def normalize(self, record: Mapping[str, Any]) -> dict[str, Any]:
        now = utcnow()
        return {
            "match_id": int(record["match_id"]),
            "start_time": _as_timestamp(record.get("start_time")),
            "duration_seconds": _as_int(record.get("duration")),
            "radiant_team_id": _as_int(record.get("radiant_team_id")),
            "radiant_name": _as_str(record.get("radiant_name")),
            "dire_team_id": _as_int(record.get("dire_team_id")),
            "dire_name": _as_str(record.get("dire_name")),
            "radiant_score": _as_int(record.get("radiant_score")),
            "dire_score": _as_int(record.get("dire_score")),
            "radiant_win": _as_bool(record.get("radiant_win")),
            "league_id": _as_int(record.get("leagueid")),
            "league_name": _as_str(record.get("league_name")),
            "series_id": _as_int(record.get("series_id")),
            "series_type": _as_int(record.get("series_type")),
            "parse_version": _as_int(record.get("version")),
            "payload": json.dumps(record, sort_keys=True, ensure_ascii=False),
            "ingested_at": now,
            "refreshed_at": now,
        }

    def close(self) -> None:
        self._client.close()
