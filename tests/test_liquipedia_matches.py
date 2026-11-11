"""Les deux flux de matchs : les liens que les tournois désignent, puis les pages rendues."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

from ingestion.core import quality, runner
from ingestion.core.config import Settings
from ingestion.core.http import HttpClient
from ingestion.core.source import SourceError
from ingestion.core.state import StopReason
from ingestion.core.warehouse import Warehouse
from ingestion.sources.liquipedia import TABLE as TOURNAMENTS
from ingestion.sources.liquipedia import VALORANT
from ingestion.sources.liquipedia_matches import (
    LINK_CHECKS,
    LINKS,
    PAGES,
    LiquipediaMatchPages,
    LiquipediaTournamentMatches,
    unpack_html,
)
from ingestion.sources.match_page import parse_match_page
from tests.test_match_page import page_capturee

FINALE = "Match:ID CHAMP24PLF R04-M002"


class FakeMatchWiki:
    """Rejoue les deux appels dont les matchs ont besoin.

    Le générateur de catégorie rend des tournois avec leurs liens, en débordant
    sur `plcontinue` comme le vrai quand il y en a trop ; `action=parse` rend
    une page, ou l'erreur `missingtitle` qu'on reçoit pour une page disparue.
    """

    def __init__(
        self,
        tournaments: list[dict[str, Any]] | None = None,
        pages: dict[str, str] | None = None,
        *,
        links_per_call: int = 2,
    ) -> None:
        self.tournaments = tournaments or []
        self.pages = pages or {}
        self.links_per_call = links_per_call
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        params = request.url.params
        if params.get("generator") == "categorymembers":
            return self._links(params)
        if params.get("action") == "parse":
            return self._parse(params["page"])
        return httpx.Response(200, json={"error": {"code": "unknown", "info": "?"}})

    def _links(self, params: httpx.QueryParams) -> httpx.Response:
        start = int(params.get("gcmcontinue") or 0)
        size = int(params["gcmlimit"])
        window = self.tournaments[start : start + size]
        flat = [(t["pageid"], link) for t in window for link in t["links"]]
        offset = int(params.get("plcontinue") or 0)
        chunk = flat[offset : offset + self.links_per_call]

        body: dict[str, Any] = {
            "query": {
                "pages": [
                    {
                        "pageid": t["pageid"],
                        "title": t["title"],
                        "links": [
                            {"ns": 130, "title": link}
                            for page_id, link in chunk
                            if page_id == t["pageid"]
                        ],
                    }
                    for t in window
                ]
            }
        }
        if offset + self.links_per_call < len(flat):
            body["continue"] = {
                "plcontinue": str(offset + self.links_per_call),
                "continue": "||",
            }
        elif start + size < len(self.tournaments):
            body["continue"] = {"gcmcontinue": str(start + size), "continue": "gcmcontinue||"}
        return httpx.Response(200, json=body)

    def _parse(self, title: str) -> httpx.Response:
        if title not in self.pages:
            return httpx.Response(
                200, json={"error": {"code": "missingtitle", "info": "page absente"}}
            )
        return httpx.Response(
            200,
            json={
                "parse": {
                    "title": title,
                    "pageid": 4242,
                    "revid": 99,
                    "text": self.pages[title],
                }
            },
        )

    def http_client(self) -> HttpClient:
        return HttpClient(
            user_agent="tests",
            client=httpx.Client(transport=httpx.MockTransport(self.handle)),
            sleeper=lambda _: None,
        )


def tournoi(page_id: int, tier: str, debut: date | None) -> dict[str, Any]:
    """Une ligne de tournoi suffisante pour ordonner la file."""
    now = datetime.now(UTC)
    row: dict[str, Any] = dict.fromkeys(TOURNAMENTS.column_names)
    row.update(
        wiki="valorant",
        page_id=page_id,
        page_title=f"Tournoi {page_id}",
        tier=tier,
        start_date=debut,
        infobox="{}",
        ingested_at=now,
        refreshed_at=now,
    )
    return row


def lien(page_id: int, titre: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "wiki": "valorant",
        "tournament_page_id": page_id,
        "match_title": titre,
        "ingested_at": now,
        "refreshed_at": now,
    }


HIER = date.today() - timedelta(days=1)
IL_Y_A_UN_AN = date.today() - timedelta(days=365)
DEMAIN = date.today() + timedelta(days=1)


# -- Les liens tournoi → matchs ---------------------------------------------


@pytest.fixture
def wiki_de_liens() -> FakeMatchWiki:
    return FakeMatchWiki(
        tournaments=[
            {"pageid": 1, "title": "VCT/2024/Champions", "links": ["Match:A 1", "Match:A 2"]},
            {"pageid": 2, "title": "Coupe locale", "links": ["Match:B 1"]},
            {"pageid": 3, "title": "Tournoi de 2020", "links": []},
            {"pageid": 4, "title": "Autre tournoi de 2020", "links": []},
            {"pageid": 5, "title": "Masters", "links": ["Match:C 1", "Match:A 2"]},
        ]
    )


def source_de_liens(
    wiki: FakeMatchWiki, settings: Settings, taille: int = 2
) -> LiquipediaTournamentMatches:
    return LiquipediaTournamentMatches(
        VALORANT, settings=settings, client=wiki.http_client(), batch_size=taille
    )


def test_les_liens_qui_debordent_sont_suivis_jusqu_au_bout(
    wiki_de_liens: FakeMatchWiki, settings: Settings
) -> None:
    """Trois liens à raison de deux par appel : sans `plcontinue`, le dernier se perd."""
    page = source_de_liens(wiki_de_liens, settings).fetch(None)

    assert len(wiki_de_liens.requests) == 2
    assert {(r["tournament_page_id"], r["match_title"]) for r in page.records} == {
        (1, "Match:A 1"),
        (1, "Match:A 2"),
        (2, "Match:B 1"),
    }
    assert page.next_cursor == "2"


def test_seul_l_espace_des_matchs_est_demande(
    wiki_de_liens: FakeMatchWiki, settings: Settings
) -> None:
    source_de_liens(wiki_de_liens, settings).fetch(None)

    assert wiki_de_liens.requests[0].url.params["plnamespace"] == "130"
    assert wiki_de_liens.requests[0].url.params["gcmnamespace"] == "0"


def test_une_tranche_de_tournois_sans_match_n_arrete_pas_le_balayage(
    wiki_de_liens: FakeMatchWiki, settings: Settings, warehouse: Warehouse
) -> None:
    """Les tournois de 2020 n'ont pas de page de match : ceux d'après, si."""
    report = runner.run(source_de_liens(wiki_de_liens, settings), warehouse, max_pages=10)

    assert report.stop_reason is StopReason.EXHAUSTED
    assert report.pages == 3
    assert warehouse.count(LINKS.qualified_name) == 5


def test_un_match_designe_par_deux_tournois_garde_ses_deux_liens(
    wiki_de_liens: FakeMatchWiki, settings: Settings, warehouse: Warehouse
) -> None:
    """Un tournoi et sa page mère désignent souvent la même page de match."""
    runner.run(source_de_liens(wiki_de_liens, settings), warehouse, max_pages=10)

    designe_par = warehouse.query(
        f"SELECT tournament_page_id FROM {LINKS.qualified_name} "
        "WHERE match_title = 'Match:A 2' ORDER BY 1"
    )
    assert designe_par == [(1,), (5,)]


def test_relancer_le_releve_des_liens_ne_duplique_rien(
    wiki_de_liens: FakeMatchWiki, settings: Settings, warehouse: Warehouse
) -> None:
    runner.run(source_de_liens(wiki_de_liens, settings), warehouse, max_pages=10)

    second = runner.run(source_de_liens(wiki_de_liens, settings), warehouse, max_pages=10)

    assert (second.inserted, second.updated) == (0, 5)
    assert not quality.has_blocking_failure(quality.run_checks(warehouse, LINK_CHECKS))


# -- La file des pages à rendre ---------------------------------------------


@pytest.fixture
def entrepot_avec_liens(warehouse: Warehouse) -> Warehouse:
    warehouse.ensure_table(TOURNAMENTS)
    warehouse.ensure_table(LINKS)
    warehouse.upsert(
        TOURNAMENTS,
        [
            tournoi(1, "C-Tier", HIER),
            tournoi(2, "S-Tier", IL_Y_A_UN_AN),
            tournoi(3, "S-Tier", HIER),
            tournoi(4, "S-Tier", DEMAIN),
        ],
    )
    warehouse.upsert(
        LINKS,
        [
            lien(1, "Match:mineur"),
            lien(2, "Match:majeur ancien"),
            lien(3, "Match:majeur recent"),
            lien(4, "Match:pas encore joue"),
        ],
    )
    return warehouse


def source_de_pages(
    wiki: FakeMatchWiki, settings: Settings, warehouse: Warehouse, taille: int = 2
) -> Iterator[LiquipediaMatchPages]:
    source = LiquipediaMatchPages(
        VALORANT, settings=settings, client=wiki.http_client(), batch_size=taille
    )
    warehouse.ensure_table(PAGES)
    source.attach(warehouse)
    yield source
    source.close()


@pytest.fixture
def pages(
    settings: Settings, entrepot_avec_liens: Warehouse
) -> Iterator[tuple[LiquipediaMatchPages, FakeMatchWiki]]:
    wiki = FakeMatchWiki(pages={"Match:majeur recent": page_capturee()})
    for source in source_de_pages(wiki, settings, entrepot_avec_liens):
        yield source, wiki


def test_les_tournois_majeurs_passent_d_abord_les_plus_recents_en_tete(
    pages: tuple[LiquipediaMatchPages, FakeMatchWiki],
) -> None:
    """À trente secondes la page, l'ordre de passage décide de ce qu'on aura lu ce soir."""
    source, _ = pages

    assert source.pending(10) == ["Match:majeur recent", "Match:majeur ancien", "Match:mineur"]


def test_un_tournoi_qui_n_a_pas_commence_n_entre_pas_dans_la_file(
    pages: tuple[LiquipediaMatchPages, FakeMatchWiki],
) -> None:
    source, _ = pages

    assert "Match:pas encore joue" not in source.pending(10)


def test_une_page_rendue_devient_une_ligne_complete(
    pages: tuple[LiquipediaMatchPages, FakeMatchWiki],
) -> None:
    source, wiki = pages

    page = source.fetch(None)
    ligne = source.normalize(page.records[0])

    assert wiki.requests[0].url.params["action"] == "parse"
    assert ligne["page_title"] == "Match:majeur recent"
    assert (ligne["page_id"], ligne["revision_id"]) == (4242, 99)
    assert ligne["status"] == "finished"
    assert ligne["played_at"] == datetime(2024, 8, 24, 7, 0, tzinfo=UTC)
    assert ligne["tournament_page"] == "VCT/2024/Champions"
    assert (ligne["team_1"], ligne["team_2"]) == ("Leviatán", "Team Heretics")
    assert (ligne["score_1"], ligne["score_2"], ligne["map_count"]) == (1, 3, 4)
    assert set(ligne) == set(PAGES.column_names)


def test_la_page_stockee_redonne_le_meme_document(
    pages: tuple[LiquipediaMatchPages, FakeMatchWiki],
) -> None:
    """La leçon du wikitexte : garder de quoi relire sans redemander."""
    source, _ = pages
    ligne = source.normalize(source.fetch(None).records[0])

    relue = parse_match_page(unpack_html(ligne["html_gz"]))

    assert relue == json.loads(ligne["document"])
    assert len(ligne["html_gz"]) < 20_000


def test_une_page_disparue_est_retenue_pour_ne_plus_etre_demandee(
    pages: tuple[LiquipediaMatchPages, FakeMatchWiki], entrepot_avec_liens: Warehouse
) -> None:
    source, wiki = pages

    runner.run(source, entrepot_avec_liens, max_pages=10)

    statuts = dict(
        entrepot_avec_liens.query(f"SELECT page_title, status FROM {PAGES.qualified_name}")
    )
    assert statuts == {
        "Match:majeur recent": "finished",
        "Match:majeur ancien": "missing",
        "Match:mineur": "missing",
    }
    assert source.pending(10) == []
    assert len(wiki.requests) == 3


def test_la_file_videe_arrete_le_flux_et_le_relancer_ne_demande_rien(
    pages: tuple[LiquipediaMatchPages, FakeMatchWiki], entrepot_avec_liens: Warehouse
) -> None:
    source, wiki = pages
    premier = runner.run(source, entrepot_avec_liens, max_pages=10)
    appels = len(wiki.requests)

    second = runner.run(source, entrepot_avec_liens, max_pages=10)

    assert premier.stop_reason is StopReason.EXHAUSTED
    assert (premier.pages, premier.records) == (2, 3)
    assert second.stop_reason is StopReason.EMPTY_PAGE
    assert len(wiki.requests) == appels


def test_une_execution_bornee_reprend_la_file_ou_elle_en_etait(
    pages: tuple[LiquipediaMatchPages, FakeMatchWiki], entrepot_avec_liens: Warehouse
) -> None:
    source, _ = pages

    premiere = runner.run(source, entrepot_avec_liens, max_pages=1)

    assert premiere.stop_reason is StopReason.PAGE_LIMIT
    assert source.pending(10) == ["Match:mineur"]


def test_un_match_rendu_avant_d_etre_joue_sera_redemande_plus_tard(
    pages: tuple[LiquipediaMatchPages, FakeMatchWiki], entrepot_avec_liens: Warehouse
) -> None:
    source, _ = pages
    runner.run(source, entrepot_avec_liens, max_pages=10)
    entrepot_avec_liens.query(
        f"UPDATE {PAGES.qualified_name} SET status = 'upcoming' "
        "WHERE page_title = 'Match:majeur recent'"
    )
    assert source.pending(10) == []  # rendu à l'instant : on attend

    entrepot_avec_liens.query(
        f"UPDATE {PAGES.qualified_name} SET refreshed_at = now() - INTERVAL 4 DAY "
        "WHERE page_title = 'Match:majeur recent'"
    )

    assert source.pending(10) == ["Match:majeur recent"]


def test_les_pages_rendues_satisfont_les_attentes_bloquantes(
    pages: tuple[LiquipediaMatchPages, FakeMatchWiki], entrepot_avec_liens: Warehouse
) -> None:
    source, _ = pages
    runner.run(source, entrepot_avec_liens, max_pages=10)

    resultats = quality.run_checks(entrepot_avec_liens, source.checks)

    assert not quality.has_blocking_failure(resultats)


def test_sans_les_liens_la_file_dit_quoi_collecter_d_abord(
    settings: Settings, warehouse: Warehouse
) -> None:
    source = LiquipediaMatchPages(
        VALORANT, settings=settings, client=FakeMatchWiki().http_client()
    )
    source.attach(warehouse)

    with pytest.raises(SourceError, match="collecter d'abord"):
        source.fetch(None)


def test_sans_entrepot_attache_la_file_est_illisible(settings: Settings) -> None:
    source = LiquipediaMatchPages(
        VALORANT, settings=settings, client=FakeMatchWiki().http_client()
    )

    with pytest.raises(SourceError, match="aucun entrepôt"):
        source.fetch(None)
