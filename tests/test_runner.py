"""La boucle d'ingestion, de bout en bout.

La vraie source OpenDota tourne ici contre une doublure fidèle de son API et un
entrepôt en mémoire. Ce sont les promesses du projet qui se vérifient : relancer
ne duplique rien, une interruption ne perd qu'une page, un rattrapage s'arrête
dès qu'il retrouve du connu.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ingestion.core import runner
from ingestion.core.config import Settings
from ingestion.core.http import HttpError, RetryPolicy
from ingestion.core.state import Mode, StopReason
from ingestion.core.warehouse import RUNS_TABLE, Warehouse
from ingestion.sources.opendota import TABLE, OpenDotaProMatches
from tests.conftest import FakeProMatchesApi

TABLE_NAME = TABLE.qualified_name
NOUVEAU_MATCH: dict[str, Any] = {
    "match_id": 9_000_000_000,
    "duration": 2100,
    "start_time": 1789400000,
    "radiant_team_id": 1,
    "radiant_name": "Équipe A",
    "dire_team_id": 2,
    "dire_name": "Équipe B",
    "leagueid": 1,
    "league_name": "Tournoi d'essai",
    "series_id": 1,
    "series_type": 1,
    "radiant_score": 30,
    "dire_score": 12,
    "radiant_win": True,
    "version": 22,
}


def ingere_tout(source: OpenDotaProMatches, warehouse: Warehouse) -> None:
    runner.run(source, warehouse, max_pages=10)


# -- Première collecte ------------------------------------------------------


def test_une_premiere_execution_descend_jusqu_au_bas_du_flux(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    report = runner.run(opendota, warehouse, max_pages=10)

    assert report.stop_reason is StopReason.EXHAUSTED
    assert (report.pages, report.records, report.inserted) == (3, 14, 14)
    assert warehouse.count(TABLE_NAME) == 14


def test_l_etat_encadre_exactement_ce_qui_a_ete_collecte(
    opendota: OpenDotaProMatches, warehouse: Warehouse, opendota_records: list[dict[str, Any]]
) -> None:
    runner.run(opendota, warehouse, max_pages=10)

    state = warehouse.load_state(opendota.key)
    assert state is not None
    identifiants = [int(record["match_id"]) for record in opendota_records]
    assert state.high_watermark == str(max(identifiants))
    assert state.backfill_cursor == str(min(identifiants))
    assert state.records_seen == 14


# -- Idempotence ------------------------------------------------------------


def test_relancer_la_meme_ingestion_ne_duplique_rien(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    ingere_tout(opendota, warehouse)

    second = runner.run(opendota, warehouse, max_pages=10)

    assert warehouse.count(TABLE_NAME) == 14
    assert second.inserted == 0
    assert second.updated > 0


def test_relancer_ne_reecrit_pas_la_date_de_premiere_arrivee(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    """Une ligne revue est rafraîchie, mais garde la date à laquelle elle est arrivée."""
    ingere_tout(opendota, warehouse)
    arrivees = warehouse.query(f"SELECT match_id, ingested_at FROM {TABLE_NAME} ORDER BY 1")

    ingere_tout(opendota, warehouse)

    assert (
        warehouse.query(f"SELECT match_id, ingested_at FROM {TABLE_NAME} ORDER BY 1")
        == arrivees
    )
    # Le rattrapage s'arrête à la première page déjà connue : six lignes revues,
    # les huit autres intouchées. C'est le quota qu'on économise ainsi.
    rafraichies = warehouse.scalar(
        f"SELECT count(*) FROM {TABLE_NAME} WHERE refreshed_at > ingested_at"
    )
    assert rafraichies == 6


# -- Reprise ----------------------------------------------------------------


def test_une_execution_bornee_reprend_la_ou_elle_s_est_arretee(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    premiere = runner.run(opendota, warehouse, max_pages=1)
    assert premiere.stop_reason is StopReason.PAGE_LIMIT
    assert warehouse.count(TABLE_NAME) == 6

    suite = runner.run(opendota, warehouse, mode=Mode.BACKFILL, max_pages=10)

    assert suite.stop_reason is StopReason.EXHAUSTED
    assert suite.inserted == 8
    assert warehouse.count(TABLE_NAME) == 14


def test_le_backfill_repart_sous_la_frontiere_et_pas_du_sommet(
    opendota: OpenDotaProMatches, warehouse: Warehouse, fake_api: FakeProMatchesApi
) -> None:
    runner.run(opendota, warehouse, max_pages=1)
    apres_rattrapage = warehouse.load_state(opendota.key)
    assert apres_rattrapage is not None
    premiere_requete_du_backfill = len(fake_api.requests)

    runner.run(opendota, warehouse, mode=Mode.BACKFILL, max_pages=1)

    reprise = fake_api.requests[premiere_requete_du_backfill]
    assert reprise.url.params["less_than_match_id"] == apres_rattrapage.backfill_cursor
    assert warehouse.count(TABLE_NAME) == 12


# -- Rattrapage -------------------------------------------------------------


def test_un_rattrapage_s_arrete_des_qu_il_retrouve_du_connu(
    opendota: OpenDotaProMatches, warehouse: Warehouse, fake_api: FakeProMatchesApi
) -> None:
    """Le quota ne sert qu'à collecter ce qui manque, pas à relire l'historique."""
    ingere_tout(opendota, warehouse)
    appels_avant = len(fake_api.requests)
    fake_api.add_record(NOUVEAU_MATCH)

    rattrapage = runner.run(opendota, warehouse, max_pages=10)

    assert rattrapage.stop_reason is StopReason.CAUGHT_UP
    assert rattrapage.pages == 1
    assert len(fake_api.requests) == appels_avant + 1
    assert rattrapage.inserted == 1
    assert warehouse.count(TABLE_NAME) == 15


def test_un_rattrapage_trop_court_signale_la_lacune_qu_il_laisse(
    opendota: OpenDotaProMatches, warehouse: Warehouse, fake_api: FakeProMatchesApi
) -> None:
    ingere_tout(opendota, warehouse)
    for decalage in range(12):
        fake_api.add_record({**NOUVEAU_MATCH, "match_id": 9_000_000_000 + decalage})

    rattrapage = runner.run(opendota, warehouse, max_pages=1)

    assert rattrapage.gap_suspected
    state = warehouse.load_state(opendota.key)
    assert state is not None
    assert state.backfill_cursor == "9000000006"


def test_une_premiere_execution_courte_ne_signale_aucune_lacune(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    """Rien n'est connu : il n'y a pas de trou entre l'îlot et le néant."""
    assert not runner.run(opendota, warehouse, max_pages=1).gap_suspected


# -- Interruption -----------------------------------------------------------


def test_une_panne_en_cours_de_route_conserve_les_pages_deja_ecrites(
    warehouse: Warehouse, fake_api: FakeProMatchesApi, settings: Settings
) -> None:
    source = OpenDotaProMatches(
        settings=settings,
        client=fake_api.http_client(retry=RetryPolicy(max_attempts=1)),
        page_size=fake_api.page_size,
    )
    fake_api.queued_responses.extend(
        [
            httpx.Response(200, json=fake_api.records[:6]),
            httpx.Response(503),
        ]
    )

    with pytest.raises(HttpError):
        runner.run(source, warehouse, max_pages=10)

    assert warehouse.count(TABLE_NAME) == 6
    state = warehouse.load_state(source.key)
    assert state is not None
    assert state.backfill_cursor == str(min(int(r["match_id"]) for r in fake_api.records[:6]))
    source.close()


def test_une_execution_interrompue_laisse_une_trace_d_echec(
    warehouse: Warehouse, fake_api: FakeProMatchesApi, settings: Settings
) -> None:
    source = OpenDotaProMatches(
        settings=settings,
        client=fake_api.http_client(retry=RetryPolicy(max_attempts=1)),
        page_size=fake_api.page_size,
    )
    fake_api.queued_responses.append(httpx.Response(503))

    with pytest.raises(HttpError):
        runner.run(source, warehouse, max_pages=10)

    assert warehouse.query(f"SELECT stop_reason, pages FROM {RUNS_TABLE}") == [("failed", 0)]
    source.close()


# -- Traces -----------------------------------------------------------------


def test_chaque_execution_laisse_sa_trace(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    runner.run(opendota, warehouse, max_pages=1)
    runner.run(opendota, warehouse, mode=Mode.BACKFILL, max_pages=1)

    traces = warehouse.query(
        f"SELECT mode, pages, records FROM {RUNS_TABLE} ORDER BY started_at"
    )

    assert traces == [("catchup", 1, 6), ("backfill", 1, 6)]


def test_une_execution_sans_page_est_refusee(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    with pytest.raises(ValueError, match="au moins une page"):
        runner.run(opendota, warehouse, max_pages=0)
