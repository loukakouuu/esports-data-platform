"""Liquipedia — tournois, un wiki par discipline.

Deux appels font une page de ce flux : la catégorie `Tournaments` donne des
titres, puis une seconde requête rapporte leur wikitexte. Les deux passent par
le client cadencé, qui tient l'intervalle de 2 s imposé par les conditions
d'utilisation.

Deux détails font toute la différence sur la charge imposée au serveur :

- `rvsection=0` ne rapporte que la section d'en-tête, celle qui porte
  l'infobox. Mesuré sur des pages réelles : 1,5 ko au lieu de 123 ko, soit 85
  fois moins à transférer pour la même information.
- 50 titres par requête, plutôt qu'une requête par page.

`action=parse` donnerait le même résultat mieux découpé, mais il est limité à
une requête toutes les 30 secondes : inutilisable pour 19 000 tournois. La
lecture des révisions reste sous la limite ordinaire.

Les contenus sont sous licence CC-BY-SA 3.0 et doivent être attribués.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ingestion.core.config import Settings
from ingestion.core.http import HttpClient
from ingestion.core.quality import Check, Severity
from ingestion.core.source import Page, SourceError, TokenScanSource
from ingestion.core.state import SourceKey, utcnow
from ingestion.core.table import Column, TableSpec
from ingestion.sources.wikitext import find_template, parse_fields, strip_markup

logger = logging.getLogger(__name__)

MIN_INTERVAL = 2.1
"""Les conditions d'utilisation imposent une requête toutes les 2 s ; on garde
une marge, l'horloge du serveur n'étant pas la nôtre."""

BATCH_SIZE = 50
"""Nombre de titres demandés d'un coup — le maximum admis sans droits de robot."""

INFOBOX = "Infobox league"
CATEGORY = "Category:Tournaments"
MAIN_NAMESPACE = "0"
"""Les brouillons d'utilisateurs traînent dans la catégorie : on ne collecte
que l'espace principal."""


@dataclass(frozen=True, slots=True)
class Wiki:
    """Un wiki Liquipedia, c'est-à-dire une discipline."""

    slug: str
    discipline: str

    @property
    def api(self) -> str:
        return f"https://liquipedia.net/{self.slug}/api.php"

    @property
    def key(self) -> SourceKey:
        return SourceKey(name="liquipedia", discipline=self.discipline, resource="tournaments")


COUNTERSTRIKE = Wiki(slug="counterstrike", discipline="counterstrike")
DOTA2 = Wiki(slug="dota2", discipline="dota2")
WIKIS = (COUNTERSTRIKE, DOTA2)

TABLE = TableSpec(
    schema="raw",
    name="liquipedia_tournaments",
    columns=(
        # Un identifiant de page n'est unique qu'au sein de son wiki.
        Column("wiki", "VARCHAR NOT NULL"),
        Column("page_id", "BIGINT NOT NULL"),
        Column("page_title", "VARCHAR NOT NULL"),
        Column("name", "VARCHAR"),
        Column("series", "VARCHAR"),
        Column("organizer", "VARCHAR"),
        # Le vocabulaire des tiers diffère d'un wiki à l'autre — « S-Tier » côté
        # Counter-Strike, « 1 » côté Dota 2. On garde ce que la source dit ;
        # les réconcilier est le travail de la couche de transformation.
        Column("tier", "VARCHAR"),
        Column("tier_type", "VARCHAR"),
        Column("game", "VARCHAR"),
        Column("setting", "VARCHAR"),
        Column("country", "VARCHAR"),
        Column("city", "VARCHAR"),
        Column("start_date", "DATE"),
        Column("end_date", "DATE"),
        Column("prize_pool_usd", "DOUBLE"),
        Column("prize_pool_local", "VARCHAR"),
        Column("local_currency", "VARCHAR"),
        Column("team_count", "INTEGER"),
        Column("revised_at", "TIMESTAMPTZ"),
        Column("infobox", "JSON NOT NULL"),
        Column("ingested_at", "TIMESTAMPTZ NOT NULL"),
        Column("refreshed_at", "TIMESTAMPTZ NOT NULL"),
    ),
    primary_key=("wiki", "page_id"),
    write_once=frozenset({"ingested_at"}),
)

PREMIER_TOURNOI = "1998-01-01"
"""Antérieur aux premiers tournois Counter-Strike : une date en deçà est une
erreur de saisie, pas une compétition oubliée."""

CHECKS = (
    Check.unique(
        "page_unique_par_wiki",
        description="une page apparaît deux fois pour le même wiki",
        table=TABLE.qualified_name,
        column=("wiki", "page_id"),
    ),
    Check.rows_where(
        "page_identifiee",
        description="page sans identifiant ou sans titre",
        table=TABLE.qualified_name,
        violation="page_id IS NULL OR page_id <= 0 OR page_title IS NULL",
    ),
    Check.rows_where(
        "infobox_lisible",
        description="infobox brute illisible en JSON",
        table=TABLE.qualified_name,
        violation="infobox IS NULL OR NOT json_valid(infobox)",
    ),
    Check.rows_where(
        "dates_ordonnees",
        description="tournoi qui se termine avant d'avoir commencé",
        table=TABLE.qualified_name,
        violation="start_date IS NOT NULL AND end_date IS NOT NULL AND end_date < start_date",
    ),
    Check.rows_where(
        "dates_plausibles",
        description="date antérieure aux premiers tournois, ou trop lointaine",
        table=TABLE.qualified_name,
        violation=(
            f"start_date < DATE '{PREMIER_TOURNOI}' "
            "OR start_date > current_date + INTERVAL 5 YEAR"
        ),
    ),
    Check.rows_where(
        "dotation_positive",
        description="dotation négative",
        table=TABLE.qualified_name,
        violation="prize_pool_usd < 0",
    ),
    Check.rows_where(
        "dates_coherentes",
        description="ligne rafraîchie avant d'être arrivée",
        table=TABLE.qualified_name,
        violation="refreshed_at < ingested_at",
    ),
    Check.rows_where(
        "tournoi_date",
        description="tournoi sans date de début — fréquent sur les pages anciennes",
        table=TABLE.qualified_name,
        violation="start_date IS NULL",
        severity=Severity.WARN,
    ),
    Check.rows_where(
        "tournoi_classe",
        description="tournoi sans tier",
        table=TABLE.qualified_name,
        violation="tier IS NULL",
        severity=Severity.WARN,
    ),
)


def _as_text(fields: Mapping[str, str], key: str) -> str | None:
    """Valeur nettoyée de son balisage, ou `None` si elle ne dit rien."""
    value = fields.get(key)
    if value is None:
        return None
    cleaned = strip_markup(value)
    return cleaned or None


def _as_date(fields: Mapping[str, str], key: str) -> date | None:
    """Une date Liquipedia est ISO, ou n'est pas.

    Les pages anciennes laissent le champ vide, ou n'en mettent qu'une partie
    (« 2024-03-?? »). Une date incomplète reste inconnue plutôt que devinée.
    """
    value = (fields.get(key) or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        logger.debug("date illisible pour %s : %r", key, value)
        return None


def _as_amount(fields: Mapping[str, str], key: str) -> float | None:
    """Un montant s'écrit « 1,250,000 » — parfois, c'est un modèle transclus."""
    value = (fields.get(key) or "").replace(",", "").replace("$", "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        logger.debug("dotation illisible pour %s : %r", key, fields.get(key))
        return None


def _as_count(fields: Mapping[str, str], key: str) -> int | None:
    value = (fields.get(key) or "").strip()
    try:
        return int(value)
    except ValueError:
        return None


def _as_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class LiquipediaTournaments(TokenScanSource):
    """Tournois d'un wiki Liquipedia, balayés par ordre alphabétique de titre."""

    def __init__(
        self,
        wiki: Wiki,
        *,
        settings: Settings | None = None,
        client: HttpClient | None = None,
        batch_size: int = BATCH_SIZE,
        category: str = CATEGORY,
    ) -> None:
        super().__init__(key=wiki.key, table=TABLE, checks=CHECKS)
        resolved = settings or Settings.from_env()
        self.wiki = wiki
        self._batch_size = batch_size
        self._category = category
        # L'agent se résout toujours : c'est la configuration qu'on vérifie, pas
        # le client, et un contact manquant doit se dire même en test.
        self.user_agent = _user_agent(resolved)
        self._client = client or HttpClient(
            user_agent=self.user_agent, min_interval=MIN_INTERVAL
        )

    def fetch(self, cursor: str | None) -> Page:
        listing = self._list_titles(cursor)
        if not listing.pages:
            return Page(records=(), next_cursor=None)
        contents = self._fetch_wikitext([page["pageid"] for page in listing.pages])
        records = tuple(
            {**page, **contents.get(int(page["pageid"]), {})} for page in listing.pages
        )
        return Page(records=records, next_cursor=listing.next_cursor)

    def normalize(self, record: Mapping[str, Any]) -> dict[str, Any]:
        wikitext = record.get("wikitext") or ""
        body = find_template(wikitext, INFOBOX)
        fields = parse_fields(body) if body else {}
        if not fields:
            logger.debug("aucune %s sur %r", INFOBOX, record.get("title"))
        now = utcnow()
        return {
            "wiki": self.wiki.slug,
            "page_id": int(record["pageid"]),
            "page_title": str(record["title"]),
            "name": _as_text(fields, "name"),
            "series": _as_text(fields, "series"),
            "organizer": _as_text(fields, "organizer"),
            "tier": _as_text(fields, "liquipediatier"),
            "tier_type": _as_text(fields, "liquipediatiertype"),
            "game": _as_text(fields, "game"),
            "setting": _as_text(fields, "type"),
            "country": _as_text(fields, "country"),
            "city": _as_text(fields, "city"),
            "start_date": _as_date(fields, "sdate"),
            "end_date": _as_date(fields, "edate"),
            "prize_pool_usd": _as_amount(fields, "prizepoolusd"),
            "prize_pool_local": _as_text(fields, "prizepool"),
            "local_currency": _as_text(fields, "localcurrency"),
            "team_count": _as_count(fields, "team_number"),
            "revised_at": _as_timestamp(record.get("revised_at")),
            # L'infobox brute est conservée : les champs retenus ici ne sont
            # qu'une lecture parmi d'autres, et elle changera.
            "infobox": json.dumps(fields, sort_keys=True, ensure_ascii=False),
            "ingested_at": now,
            "refreshed_at": now,
        }

    def close(self) -> None:
        self._client.close()

    # -- Les deux appels ----------------------------------------------------

    def _list_titles(self, cursor: str | None) -> _Listing:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": self._category,
            "cmnamespace": MAIN_NAMESPACE,
            "cmlimit": str(self._batch_size),
            "format": "json",
            "formatversion": "2",
        }
        if cursor is not None:
            params["cmcontinue"] = cursor
        payload = self._client.get_json(self.wiki.api, params)
        _raise_on_api_error(payload)
        members = _dig(payload, "query", "categorymembers")
        if not isinstance(members, list):
            raise SourceError(f"{self._category} : liste de pages attendue")
        cont = payload.get("continue") if isinstance(payload, dict) else None
        next_cursor = cont.get("cmcontinue") if isinstance(cont, dict) else None
        return _Listing(
            pages=[m for m in members if isinstance(m, dict) and "pageid" in m],
            next_cursor=next_cursor if isinstance(next_cursor, str) else None,
        )

    def _fetch_wikitext(self, page_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Rapporte la seule section d'en-tête des pages demandées."""
        payload = self._client.get_json(
            self.wiki.api,
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content|timestamp",
                "rvslots": "main",
                "rvsection": "0",
                "pageids": "|".join(str(page_id) for page_id in page_ids),
                "format": "json",
                "formatversion": "2",
            },
        )
        _raise_on_api_error(payload)
        pages = _dig(payload, "query", "pages")
        if not isinstance(pages, list):
            raise SourceError("révisions : liste de pages attendue")

        contents: dict[int, dict[str, Any]] = {}
        for page in pages:
            if not isinstance(page, dict) or page.get("missing"):
                continue
            revisions = page.get("revisions") or [{}]
            revision = revisions[0] if isinstance(revisions[0], dict) else {}
            slot = _dig(revision, "slots", "main")
            contents[int(page["pageid"])] = {
                "wikitext": slot.get("content") if isinstance(slot, dict) else None,
                "revised_at": revision.get("timestamp"),
            }
        return contents


@dataclass(frozen=True, slots=True)
class _Listing:
    pages: list[dict[str, Any]]
    next_cursor: str | None


def _dig(payload: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(payload, dict):
            return None
        payload = payload.get(key)
    return payload


def _raise_on_api_error(payload: Any) -> None:
    """MediaWiki répond 200 même quand il refuse : l'erreur est dans le corps."""
    if isinstance(payload, dict) and "error" in payload:
        error = payload["error"]
        code = error.get("code") if isinstance(error, dict) else None
        info = error.get("info") if isinstance(error, dict) else error
        raise SourceError(f"API MediaWiki : {code} — {info}")


def _user_agent(settings: Settings) -> str:
    """Les conditions d'utilisation demandent un agent identifiant avec contact.

    Ce contact n'a rien à faire dans le dépôt : il vient de l'environnement. À
    défaut, l'agent par défaut nomme le projet et son dépôt — descriptif, mais
    sans adresse où écrire, d'où l'avertissement.
    """
    if settings.liquipedia_user_agent:
        return settings.liquipedia_user_agent
    logger.warning(
        "LIQUIPEDIA_USER_AGENT n'est pas renseigné : les conditions d'utilisation "
        "demandent un contact dans le User-Agent (voir .env.example)"
    )
    return settings.user_agent
