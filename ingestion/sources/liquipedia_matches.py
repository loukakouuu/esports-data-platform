"""Liquipedia — matchs, en deux temps.

Un match Valorant vit dans sa propre page, et cette page ne dit presque rien
tant qu'elle n'est pas rendue : les scores viennent de l'API de Riot, que
Liquipedia interroge au moment d'afficher. Il faut donc demander le rendu —
`action=parse` — et les conditions d'utilisation le limitent à **une requête
toutes les trente secondes**. Vingt mille matchs font une semaine de collecte.

D'où deux flux, parce qu'à ce prix on ne rend pas les pages dans le désordre :

- `tournament_matches` relève, pour chaque tournoi, les pages de match qu'il
  désigne. C'est rapide — une requête pour cinquante tournois — et cela donne
  ce qu'aucune autre source du projet n'offrait : le lien exact entre un match
  et son tournoi, par identifiant et non par ressemblance de noms.
- `match_pages` rend les pages une à une, **les tournois majeurs d'abord**. Sa
  file de travail n'est pas sur le réseau mais dans l'entrepôt : ce que le
  premier flux a désigné et que le second n'a pas encore lu.

Chaque page rendue est conservée, allégée et compressée, à côté du document
qu'on en tire. Une erreur de lecture se corrigera en relisant l'entrepôt, pas
en redemandant vingt mille pages.

Les contenus sont sous licence CC-BY-SA 3.0 et doivent être attribués.
"""

from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ingestion.core.config import Settings
from ingestion.core.http import HttpClient
from ingestion.core.quality import Check, Severity
from ingestion.core.source import Page, SourceError, TokenScanSource
from ingestion.core.state import utcnow
from ingestion.core.table import Column, TableSpec
from ingestion.sources.liquipedia import (
    BATCH_SIZE,
    CATEGORY,
    MAIN_NAMESPACE,
    MIN_INTERVAL,
    Wiki,
    _dig,
    _raise_on_api_error,
    _user_agent,
)
from ingestion.sources.liquipedia import (
    TABLE as TOURNAMENTS,
)
from ingestion.sources.match_page import compact_html, parse_match_page

if TYPE_CHECKING:
    from ingestion.core.warehouse import Warehouse

logger = logging.getLogger(__name__)

MATCH_NAMESPACE = "130"
"""L'espace de noms `Match:` — une page par match."""

LINKS_PER_REQUEST = "500"

PARSE_INTERVAL = 30.5
"""`action=parse` : une requête toutes les trente secondes, pas une de plus."""

PARSE_BATCH = 4
"""Pages rendues entre deux écritures. Une coupure en coûte au plus quatre,
soit deux minutes de collecte."""

RETRY_UNFINISHED_AFTER_DAYS = 3
"""Un match rendu avant d'être joué sera redemandé, mais pas avant trois jours."""

MAX_CONTINUATIONS = 40

# -- Les liens tournoi → matchs ---------------------------------------------

LINKS = TableSpec(
    schema="raw",
    name="liquipedia_tournament_matches",
    columns=(
        Column("wiki", "VARCHAR NOT NULL"),
        Column("tournament_page_id", "BIGINT NOT NULL"),
        Column("match_title", "VARCHAR NOT NULL"),
        Column("ingested_at", "TIMESTAMPTZ NOT NULL"),
        Column("refreshed_at", "TIMESTAMPTZ NOT NULL"),
    ),
    # Une page de match peut être désignée par un tournoi et par sa page mère.
    primary_key=("wiki", "tournament_page_id", "match_title"),
    write_once=frozenset({"ingested_at"}),
)

LINK_CHECKS = (
    Check.unique(
        "lien_unique",
        description="un même lien tournoi → match apparaît deux fois",
        table=LINKS.qualified_name,
        column=("wiki", "tournament_page_id", "match_title"),
    ),
    Check.rows_where(
        "lien_vers_un_match",
        description="lien qui ne vise pas l'espace de noms des matchs",
        table=LINKS.qualified_name,
        violation="match_title NOT LIKE 'Match:%'",
    ),
)


class LiquipediaTournamentMatches(TokenScanSource):
    """Pour chaque tournoi d'un wiki, les pages de match qu'il désigne."""

    def __init__(
        self,
        wiki: Wiki,
        *,
        settings: Settings | None = None,
        client: HttpClient | None = None,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        super().__init__(key=wiki.flux("tournament_matches"), table=LINKS, checks=LINK_CHECKS)
        resolved = settings or Settings.from_env()
        self.wiki = wiki
        self._batch_size = batch_size
        self.user_agent = _user_agent(resolved)
        self._client = client or HttpClient(
            user_agent=self.user_agent, min_interval=MIN_INTERVAL
        )

    def fetch(self, cursor: str | None) -> Page:
        """Une tranche de tournois, et tous leurs liens.

        La catégorie sert de générateur : MediaWiki rend les tournois et leurs
        liens d'un seul appel. Quand les liens débordent, il rend un jeton
        `plcontinue` qu'il faut épuiser avant d'avancer dans la catégorie —
        sans quoi les derniers liens de la tranche seraient perdus en silence.
        """
        params: dict[str, str] = {
            "action": "query",
            "generator": "categorymembers",
            "gcmtitle": CATEGORY,
            "gcmnamespace": MAIN_NAMESPACE,
            "gcmlimit": str(self._batch_size),
            "prop": "links",
            "plnamespace": MATCH_NAMESPACE,
            "pllimit": LINKS_PER_REQUEST,
            "format": "json",
            "formatversion": "2",
        }
        if cursor is not None:
            params["gcmcontinue"] = cursor

        links: dict[tuple[int, str], None] = {}
        continuation: Mapping[str, Any] = {}
        for _ in range(MAX_CONTINUATIONS):
            payload = self._client.get_json(self.wiki.api, params)
            _raise_on_api_error(payload)
            for page in _dig(payload, "query", "pages") or []:
                if not isinstance(page, dict) or "pageid" not in page:
                    continue
                for link in page.get("links") or []:
                    if isinstance(link, dict) and isinstance(link.get("title"), str):
                        links[(int(page["pageid"]), link["title"])] = None
            found = payload.get("continue") if isinstance(payload, dict) else None
            continuation = found if isinstance(found, dict) else {}
            if "plcontinue" not in continuation:
                break
            params = {**params, **{key: str(value) for key, value in continuation.items()}}
        else:
            raise SourceError(
                f"{self.key} : liens toujours incomplets après {MAX_CONTINUATIONS} appels"
            )

        next_cursor = continuation.get("gcmcontinue")
        return Page(
            records=tuple(
                {"tournament_page_id": page_id, "match_title": title}
                for page_id, title in links
            ),
            next_cursor=next_cursor if isinstance(next_cursor, str) else None,
        )

    def normalize(self, record: Mapping[str, Any]) -> dict[str, Any]:
        now = utcnow()
        return {
            "wiki": self.wiki.slug,
            "tournament_page_id": int(record["tournament_page_id"]),
            "match_title": str(record["match_title"]),
            "ingested_at": now,
            "refreshed_at": now,
        }

    def close(self) -> None:
        self._client.close()


# -- Les pages de match rendues ---------------------------------------------

PAGES = TableSpec(
    schema="raw",
    name="liquipedia_match_pages",
    columns=(
        Column("wiki", "VARCHAR NOT NULL"),
        Column("page_title", "VARCHAR NOT NULL"),
        Column("page_id", "BIGINT"),
        Column("revision_id", "BIGINT"),
        Column("status", "VARCHAR NOT NULL"),
        Column("played_at", "TIMESTAMPTZ"),
        Column("tournament_page", "VARCHAR"),
        Column("team_1", "VARCHAR"),
        Column("team_2", "VARCHAR"),
        Column("score_1", "INTEGER"),
        Column("score_2", "INTEGER"),
        Column("map_count", "INTEGER NOT NULL"),
        # Le document complet : cartes, joueurs, manches. La transformation le
        # déplie ; ici, il reste d'un seul tenant.
        Column("document", "JSON NOT NULL"),
        # La page elle-même, allégée et compressée : de quoi relire sans
        # redemander, le jour où le parseur devra en tirer autre chose.
        Column("html_gz", "BLOB"),
        Column("ingested_at", "TIMESTAMPTZ NOT NULL"),
        Column("refreshed_at", "TIMESTAMPTZ NOT NULL"),
    ),
    primary_key=("wiki", "page_title"),
    write_once=frozenset({"ingested_at"}),
)

STATUS_MISSING = "missing"
"""La page désignée n'existe pas — ou plus. On le retient pour ne pas la redemander."""

SETTLED = ("finished", STATUS_MISSING, "unparsed")
"""Statuts définitifs : la page ne sera pas rendue une seconde fois."""

PAGE_CHECKS = (
    Check.unique(
        "page_de_match_unique",
        description="une page de match apparaît deux fois pour le même wiki",
        table=PAGES.qualified_name,
        column=("wiki", "page_title"),
    ),
    Check.rows_where(
        "document_lisible",
        description="document de match illisible en JSON",
        table=PAGES.qualified_name,
        violation="document IS NULL OR NOT json_valid(document)",
    ),
    Check.rows_where(
        "date_plausible",
        description="match daté d'avant la sortie du jeu, ou de trop loin",
        table=PAGES.qualified_name,
        violation=(
            "played_at < TIMESTAMPTZ '2020-01-01' OR played_at > now() + INTERVAL 1 YEAR"
        ),
    ),
    Check.rows_where(
        "autant_de_cartes_que_de_points",
        description=(
            "match terminé dont le score ne correspond pas aux cartes lues — "
            "carte gagnée par forfait, ou page incomplète"
        ),
        table=PAGES.qualified_name,
        violation=(
            "status = 'finished' AND score_1 IS NOT NULL AND score_2 IS NOT NULL "
            "AND score_1 + score_2 <> map_count"
        ),
        severity=Severity.WARN,
    ),
    Check.rows_where(
        "match_termine_avec_score",
        description="match annoncé terminé mais sans score — forfait ou page incomplète",
        table=PAGES.qualified_name,
        violation="status = 'finished' AND (score_1 IS NULL OR score_2 IS NULL)",
        severity=Severity.WARN,
    ),
    Check.rows_where(
        "page_lisible",
        description="page sans bloc de match exploitable — ancien format",
        table=PAGES.qualified_name,
        violation="status = 'unparsed'",
        severity=Severity.WARN,
    ),
)

# L'ordre de passage n'est pas un classement : il dit seulement quoi rendre
# d'abord quand chaque page coûte trente secondes.
_QUEUE = f"""
WITH designes AS (
    SELECT
        liens.match_title,
        min(CASE lower(tournois.tier)
                WHEN 's-tier' THEN 1 WHEN 'a-tier' THEN 2
                WHEN 'b-tier' THEN 3 WHEN 'c-tier' THEN 4 ELSE 5 END) AS rang,
        max(tournois.start_date) AS debut
    FROM {LINKS.qualified_name} AS liens
    JOIN {TOURNAMENTS.qualified_name} AS tournois
        ON tournois.wiki = liens.wiki AND tournois.page_id = liens.tournament_page_id
    WHERE liens.wiki = ?
        AND (tournois.start_date IS NULL OR tournois.start_date <= current_date)
    GROUP BY liens.match_title
)
SELECT designes.match_title
FROM designes
LEFT JOIN {PAGES.qualified_name} AS pages
    ON pages.wiki = ? AND pages.page_title = designes.match_title
WHERE pages.page_title IS NULL
    OR (pages.status NOT IN ({", ".join(f"'{status}'" for status in SETTLED)})
        AND pages.refreshed_at < now() - INTERVAL {RETRY_UNFINISHED_AFTER_DAYS} DAY)
ORDER BY designes.rang, designes.debut DESC NULLS LAST, designes.match_title
LIMIT ?
"""


class LiquipediaMatchPages(TokenScanSource):
    """Pages de match rendues, les tournois majeurs d'abord.

    Ce flux n'a pas de curseur à lui : sa file est ce que l'entrepôt désigne et
    qu'il n'a pas encore lu. La reprise est donc gratuite — ce qui est écrit
    sort de la file — et `catchup` comme `backfill` font la même chose.
    """

    def __init__(
        self,
        wiki: Wiki,
        *,
        settings: Settings | None = None,
        client: HttpClient | None = None,
        batch_size: int = PARSE_BATCH,
    ) -> None:
        super().__init__(key=wiki.flux("match_pages"), table=PAGES, checks=PAGE_CHECKS)
        resolved = settings or Settings.from_env()
        self.wiki = wiki
        self._batch_size = batch_size
        self._warehouse: Warehouse | None = None
        self.user_agent = _user_agent(resolved)
        self._client = client or HttpClient(
            user_agent=self.user_agent, min_interval=PARSE_INTERVAL
        )

    def attach(self, warehouse: Warehouse) -> None:
        self._warehouse = warehouse

    def pending(self, limit: int) -> list[str]:
        """Les prochaines pages à rendre, par ordre de priorité."""
        if self._warehouse is None:
            raise SourceError(f"{self.key} : aucun entrepôt attaché, la file est illisible")
        for table in (LINKS, TOURNAMENTS):
            if not self._warehouse.table_exists(table.qualified_name):
                raise SourceError(
                    f"{self.key} : {table.qualified_name} n'existe pas encore — collecter "
                    f"d'abord les tournois puis leurs liens vers les matchs"
                )
        rows = self._warehouse.query(_QUEUE, [self.wiki.slug, self.wiki.slug, limit])
        return [str(row[0]) for row in rows]

    def fetch(self, cursor: str | None) -> Page:
        del cursor  # la file se lit dans l'entrepôt, pas dans un jeton
        titles = self.pending(self._batch_size + 1)
        records = tuple(self._render(title) for title in titles[: self._batch_size])
        # Le jeton ne sert qu'à dire au lanceur qu'il reste du travail.
        return Page(records=records, next_cursor="file" if len(titles) > len(records) else None)

    def _render(self, title: str) -> dict[str, Any]:
        payload = self._client.get_json(
            self.wiki.api,
            {
                "action": "parse",
                "page": title,
                "prop": "text|revid",
                "format": "json",
                "formatversion": "2",
            },
        )
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get("code") == "missingtitle":
            logger.info("%s : page absente, retenue comme telle — %s", self.key, title)
            return {"title": title, "missing": True}
        _raise_on_api_error(payload)
        parsed = _dig(payload, "parse")
        if not isinstance(parsed, dict) or not isinstance(parsed.get("text"), str):
            raise SourceError(f"{self.key} : rendu sans contenu pour {title}")
        return {
            "title": title,
            "pageid": parsed.get("pageid"),
            "revid": parsed.get("revid"),
            "html": parsed["text"],
        }

    def normalize(self, record: Mapping[str, Any]) -> dict[str, Any]:
        now = utcnow()
        html = record.get("html")
        document: dict[str, Any]
        compact: str | None = None
        if record.get("missing") or not isinstance(html, str):
            document = {"status": STATUS_MISSING, "teams": [], "veto": [], "maps": []}
        else:
            document = parse_match_page(html)
            compact = compact_html(html)

        teams = document.get("teams") or []
        score = document.get("score") or [None, None]
        epoch = document.get("played_at_epoch")
        return {
            "wiki": self.wiki.slug,
            "page_title": str(record["title"]),
            "page_id": _as_int(record.get("pageid")),
            "revision_id": _as_int(record.get("revid")),
            "status": str(document.get("status") or "unknown"),
            "played_at": datetime.fromtimestamp(epoch, tz=UTC) if epoch else None,
            "tournament_page": document.get("tournament_page"),
            "team_1": teams[0].get("page") if len(teams) > 0 else None,
            "team_2": teams[1].get("page") if len(teams) > 1 else None,
            "score_1": score[0],
            "score_2": score[1],
            "map_count": len(document.get("maps") or []),
            "document": json.dumps(document, ensure_ascii=False),
            "html_gz": pack_html(compact) if compact else None,
            "ingested_at": now,
            "refreshed_at": now,
        }

    def close(self) -> None:
        self._client.close()


def pack_html(html: str) -> bytes:
    """Compresse une page allégée. `mtime=0` : deux fois la même page, mêmes octets."""
    return gzip.compress(html.encode("utf-8"), compresslevel=9, mtime=0)


def unpack_html(blob: bytes) -> str:
    return gzip.decompress(blob).decode("utf-8")


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
