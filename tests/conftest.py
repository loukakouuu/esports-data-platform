"""Doublures partagées par les tests.

L'API d'OpenDota est rejouée plutôt que simulée à la main : le faux serveur
applique les mêmes règles que le vrai — tri décroissant, curseur strictement
exclusif, page pleine tant qu'il reste des matchs — et sert des réponses
réellement capturées. Un test qui passe contre cette doublure dit quelque chose.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from ingestion.core.config import Settings
from ingestion.core.http import HttpClient
from ingestion.core.warehouse import Warehouse
from ingestion.sources.liquipedia import COUNTERSTRIKE, LiquipediaTournaments
from ingestion.sources.opendota import OpenDotaProMatches

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_PAGES = ("p1", "p2", "p3")

Record = dict[str, Any]


def load_page(name: str) -> list[Record]:
    path = FIXTURES / f"opendota_pro_matches_{name}.json"
    loaded: list[Record] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


class FakeProMatchesApi:
    """Rejoue `GET /api/proMatches` à partir d'un jeu d'enregistrements."""

    def __init__(self, records: Sequence[Record], *, page_size: int = 6) -> None:
        self.records = sorted(records, key=lambda record: -int(record["match_id"]))
        self.page_size = page_size
        self.requests: list[httpx.Request] = []
        self.queued_responses: list[httpx.Response] = []
        """Réponses servies avant les vraies : pannes, quotas, corps invalides."""

    def add_record(self, record: Record) -> None:
        """Simule l'arrivée d'un match, comme le ferait le circuit professionnel."""
        self.records = sorted([*self.records, record], key=lambda r: -int(r["match_id"]))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.queued_responses:
            return self.queued_responses.pop(0)
        raw_cursor = request.url.params.get("less_than_match_id")
        cursor = None if raw_cursor is None else int(raw_cursor)
        below = [
            record
            for record in self.records
            if cursor is None or int(record["match_id"]) < cursor
        ]
        return httpx.Response(200, json=below[: self.page_size])

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def http_client(self, **kwargs: Any) -> HttpClient:
        """Client du projet branché sur la doublure, sans cadence ni attente."""
        kwargs.setdefault("user_agent", "tests")
        kwargs.setdefault("sleeper", lambda _: None)
        return HttpClient(client=self.client(), **kwargs)


class FakeMediaWikiApi:
    """Rejoue l'API MediaWiki de Liquipedia : catégorie puis révisions.

    Les deux points d'accès sont servis par la même doublure, à partir d'un
    corpus de pages réellement capturées. Le jeton de continuation est opaque,
    comme le vrai : la source ne peut rien en déduire.
    """

    def __init__(self, pages: Sequence[Record], *, batch_size: int = 3) -> None:
        self.pages = list(pages)
        self.batch_size = batch_size
        self.requests: list[httpx.Request] = []
        self.queued_responses: list[httpx.Response] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.queued_responses:
            return self.queued_responses.pop(0)
        params = request.url.params
        if params.get("list") == "categorymembers":
            return self._category(params)
        if params.get("prop") == "revisions":
            return self._revisions(params)
        return httpx.Response(
            200, json={"error": {"code": "unknownaction", "info": "action inconnue"}}
        )

    def _category(self, params: httpx.QueryParams) -> httpx.Response:
        raw_cursor = params.get("cmcontinue")
        start = int(raw_cursor.rsplit("|", 1)[-1]) if raw_cursor else 0
        limit = int(params.get("cmlimit") or self.batch_size)
        window = self.pages[start : start + limit]
        body: dict[str, Any] = {
            "query": {
                "categorymembers": [
                    {"pageid": page["pageid"], "ns": 0, "title": page["title"]}
                    for page in window
                ]
            }
        }
        if start + limit < len(self.pages):
            body["continue"] = {"cmcontinue": f"page|{start + limit}", "continue": "-||"}
        return httpx.Response(200, json=body)

    def _revisions(self, params: httpx.QueryParams) -> httpx.Response:
        wanted = [int(value) for value in (params.get("pageids") or "").split("|") if value]
        connus = {int(page["pageid"]): page for page in self.pages}
        rendered = []
        for page_id in wanted:
            page = connus.get(page_id)
            if page is None:
                # MediaWiki annonce les pages absentes plutôt que de les taire.
                rendered.append({"pageid": page_id, "missing": True, "title": "?"})
                continue
            contenu = page["wikitext"]
            if params.get("rvsection") == "0":
                contenu = contenu.split("\n==", 1)[0]
            rendered.append(
                {
                    "pageid": page_id,
                    "title": page["title"],
                    "revisions": [
                        {
                            "timestamp": page["revised_at"],
                            "slots": {"main": {"content": contenu}},
                        }
                    ],
                }
            )
        return httpx.Response(200, json={"query": {"pages": rendered}})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def http_client(self, **kwargs: Any) -> HttpClient:
        kwargs.setdefault("user_agent", "tests")
        kwargs.setdefault("sleeper", lambda _: None)
        return HttpClient(client=self.client(), **kwargs)


def load_liquipedia_corpus(wiki: str | None = None) -> list[Record]:
    """Pages Liquipedia capturées, filtrées sur un wiki au besoin."""
    pages: list[Record] = json.loads(
        (FIXTURES / "liquipedia_tournaments.json").read_text(encoding="utf-8")
    )
    return [page for page in pages if wiki is None or page["wiki"] == wiki]


@pytest.fixture
def liquipedia_pages() -> list[Record]:
    return load_liquipedia_corpus("counterstrike")


@pytest.fixture
def fake_wiki(liquipedia_pages: list[Record]) -> FakeMediaWikiApi:
    return FakeMediaWikiApi(liquipedia_pages)


@pytest.fixture
def liquipedia(
    fake_wiki: FakeMediaWikiApi, settings: Settings
) -> Iterator[LiquipediaTournaments]:
    """La vraie source Liquipedia, branchée sur la doublure."""
    source = LiquipediaTournaments(
        COUNTERSTRIKE,
        settings=settings,
        client=fake_wiki.http_client(),
        batch_size=fake_wiki.batch_size,
    )
    yield source
    source.close()


@pytest.fixture
def opendota_records() -> list[Record]:
    """Les 14 matchs capturés, du plus récent au plus ancien."""
    return [record for name in FIXTURE_PAGES for record in load_page(name)]


@pytest.fixture
def fake_api(opendota_records: list[Record]) -> FakeProMatchesApi:
    return FakeProMatchesApi(opendota_records)


@pytest.fixture
def warehouse() -> Iterator[Warehouse]:
    with Warehouse.in_memory() as opened:
        yield opened


@pytest.fixture
def settings() -> Settings:
    """Paramètres neutres : ni clé d'API, ni entrepôt sur disque."""
    return Settings(warehouse_path=Path("mémoire"), user_agent="tests")


@pytest.fixture
def opendota(fake_api: FakeProMatchesApi, settings: Settings) -> Iterator[OpenDotaProMatches]:
    """La vraie source, branchée sur la doublure."""
    source = OpenDotaProMatches(
        settings=settings,
        client=fake_api.http_client(),
        page_size=fake_api.page_size,
    )
    yield source
    source.close()
