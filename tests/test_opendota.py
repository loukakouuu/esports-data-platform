"""La source OpenDota : pagination, normalisation, et refus de ce qui est douteux."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from ingestion.core.config import Settings
from ingestion.core.source import SourceError
from ingestion.sources.opendota import TABLE, OpenDotaProMatches
from tests.conftest import FakeProMatchesApi, load_page

MATCH_COMPLET: dict[str, Any] = {
    "match_id": 8997682537,
    "duration": 1975,
    "start_time": 1789324066,
    "radiant_team_id": 10241688,
    "radiant_name": "YBN Club",
    "dire_team_id": 10241694,
    "dire_name": "Stray Club",
    "leagueid": 20159,
    "league_name": "WINLINE Star Series Season 4",
    "series_id": 1141801,
    "series_type": 1,
    "radiant_score": 42,
    "dire_score": 15,
    "radiant_win": True,
    "version": 22,
}


# -- Normalisation ----------------------------------------------------------


def test_les_champs_sont_traduits_dans_le_vocabulaire_de_la_table(
    opendota: OpenDotaProMatches,
) -> None:
    row = opendota.normalize(MATCH_COMPLET)

    assert row["match_id"] == 8997682537
    assert row["duration_seconds"] == 1975
    assert row["league_id"] == 20159
    assert row["parse_version"] == 22
    assert row["radiant_win"] is True
    assert row["dire_name"] == "Stray Club"


def test_l_heure_epoch_devient_un_instant_utc(opendota: OpenDotaProMatches) -> None:
    row = opendota.normalize(MATCH_COMPLET)

    assert row["start_time"] == datetime(2026, 9, 13, 18, 27, 46, tzinfo=UTC)


def test_une_ligne_normalisee_epouse_exactement_la_table(
    opendota: OpenDotaProMatches,
) -> None:
    """Le moindre écart de schéma doit se voir ici, pas trois couches plus loin."""
    row = opendota.normalize(MATCH_COMPLET)

    assert set(row) == set(TABLE.column_names)
    TABLE.row_to_tuple(row)


def test_les_valeurs_absentes_le_restent(opendota: OpenDotaProMatches) -> None:
    """Une équipe sans identifiant reste inconnue : on ne comble pas les trous."""
    sans_equipe = {**MATCH_COMPLET, "radiant_team_id": None, "radiant_name": None}

    row = opendota.normalize(sans_equipe)

    assert row["radiant_team_id"] is None
    assert row["radiant_name"] is None
    assert row["dire_name"] == "Stray Club"


def test_un_champ_du_mauvais_type_vaut_inconnu(opendota: OpenDotaProMatches) -> None:
    abime = {**MATCH_COMPLET, "duration": "inconnu", "radiant_win": "oui"}

    row = opendota.normalize(abime)

    assert row["duration_seconds"] is None
    assert row["radiant_win"] is None


def test_la_charge_utile_complete_est_conservee(opendota: OpenDotaProMatches) -> None:
    """Le jour où l'API ajoute un champ, l'historique ne sera pas à recollecter."""
    row = opendota.normalize(MATCH_COMPLET)

    assert json.loads(row["payload"]) == MATCH_COMPLET


def test_la_date_d_arrivee_et_celle_de_rafraichissement_partent_ensemble(
    opendota: OpenDotaProMatches,
) -> None:
    row = opendota.normalize(MATCH_COMPLET)

    assert row["ingested_at"] == row["refreshed_at"]


# -- Pagination -------------------------------------------------------------


def test_la_premiere_page_part_sans_curseur(
    opendota: OpenDotaProMatches, fake_api: FakeProMatchesApi
) -> None:
    page = opendota.fetch(None)

    assert "less_than_match_id" not in fake_api.requests[0].url.params
    assert len(page.records) == 6
    assert page.next_cursor == str(min(int(r["match_id"]) for r in page.records))


def test_le_curseur_demande_bien_ce_qui_est_en_dessous(
    opendota: OpenDotaProMatches, fake_api: FakeProMatchesApi
) -> None:
    premiere = opendota.fetch(None)
    assert premiere.next_cursor is not None

    seconde = opendota.fetch(premiere.next_cursor)

    assert fake_api.requests[1].url.params["less_than_match_id"] == premiere.next_cursor
    assert max(int(r["match_id"]) for r in seconde.records) < int(premiere.next_cursor)


def test_une_page_incomplete_marque_le_bas_du_flux(opendota: OpenDotaProMatches) -> None:
    derniere_page = load_page("p3")
    curseur = str(max(int(r["match_id"]) for r in derniere_page) + 1)

    page = opendota.fetch(curseur)

    assert len(page.records) == 2
    assert page.next_cursor is None


def test_un_flux_epuise_rend_une_page_vide(opendota: OpenDotaProMatches) -> None:
    page = opendota.fetch("1")

    assert page.records == ()
    assert page.next_cursor is None


# -- Refus ------------------------------------------------------------------


def test_une_reponse_qui_n_est_pas_une_liste_est_refusee(
    opendota: OpenDotaProMatches, fake_api: FakeProMatchesApi
) -> None:
    fake_api.queued_responses.append(httpx.Response(200, json={"error": "rate limited"}))

    with pytest.raises(SourceError, match="liste attendue"):
        opendota.fetch(None)


def test_un_enregistrement_sans_identifiant_est_ecarte(
    opendota: OpenDotaProMatches, fake_api: FakeProMatchesApi
) -> None:
    fake_api.queued_responses.append(
        httpx.Response(200, json=[MATCH_COMPLET, {"duration": 10}])
    )

    page = opendota.fetch(None)

    assert len(page.records) == 1


def test_une_source_qui_n_avance_plus_est_arretee(
    opendota: OpenDotaProMatches, fake_api: FakeProMatchesApi
) -> None:
    """Si l'API ignore le curseur, insister ferait tourner la boucle sur place."""
    fake_api.queued_responses.append(httpx.Response(200, json=[MATCH_COMPLET]))

    with pytest.raises(SourceError, match="n'avance plus"):
        opendota.fetch("1000")


# -- Clé d'API --------------------------------------------------------------


def test_aucune_cle_n_est_envoyee_quand_il_n_y_en_a_pas(
    opendota: OpenDotaProMatches, fake_api: FakeProMatchesApi
) -> None:
    opendota.fetch(None)

    assert "api_key" not in fake_api.requests[0].url.params


def test_la_cle_configuree_accompagne_la_requete(fake_api: FakeProMatchesApi) -> None:
    source = OpenDotaProMatches(
        settings=Settings(
            warehouse_path=Path("mémoire"), user_agent="tests", opendota_api_key="secret"
        ),
        client=fake_api.http_client(),
        page_size=fake_api.page_size,
    )

    source.fetch(None)

    assert fake_api.requests[0].url.params["api_key"] == "secret"
    source.close()
