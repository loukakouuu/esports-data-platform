"""La couche dbt, éprouvée sur un entrepôt bâti depuis les instantanés.

Les modèles ne valent que s'ils tournent. Ce test construit un entrepôt neuf à
partir des mêmes pages capturées que le reste de la suite, puis lance `dbt
build` dessus : modèles, graine et tests de données compris. La CI y gagne une
vérification de bout en bout — ingestion, transformation, attentes — sans
toucher au réseau.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ingestion.core import runner
from ingestion.core.config import Settings
from ingestion.core.warehouse import Warehouse
from ingestion.sources.liquipedia import (
    COUNTERSTRIKE,
    DOTA2,
    VALORANT,
    LiquipediaTournaments,
)
from ingestion.sources.liquipedia import (
    TABLE as TOURNAMENTS,
)
from ingestion.sources.liquipedia_matches import LINKS, LiquipediaMatchPages
from ingestion.sources.opendota import OpenDotaProMatches
from tests.conftest import FakeMediaWikiApi, FakeProMatchesApi, load_liquipedia_corpus
from tests.test_liquipedia_matches import FINALE, HIER, FakeMatchWiki, lien, tournoi
from tests.test_match_page import page_capturee

TRANSFORM = Path(__file__).resolve().parents[1] / "transform"


def _ingerer(chemin: Path, fake_api: FakeProMatchesApi, settings: Settings) -> None:
    """Remplit un entrepôt neuf avec tous les flux, doublures à l'appui."""
    with Warehouse.open(chemin) as warehouse:
        opendota = OpenDotaProMatches(
            settings=settings, client=fake_api.http_client(), page_size=fake_api.page_size
        )
        runner.run(opendota, warehouse, max_pages=10)
        opendota.close()

        for wiki in (COUNTERSTRIKE, DOTA2):
            pages = load_liquipedia_corpus(wiki.slug)
            doublure = FakeMediaWikiApi(pages)
            source = LiquipediaTournaments(
                wiki,
                settings=settings,
                client=doublure.http_client(),
                batch_size=doublure.batch_size,
            )
            runner.run(source, warehouse, max_pages=10)
            source.close()

        # Valorant : un tournoi, le match qu'il désigne, et sa page rendue.
        champions = {**tournoi(378836, "S-Tier", HIER), "page_title": "VCT/2024/Champions"}
        warehouse.upsert(TOURNAMENTS, [champions])
        warehouse.ensure_table(LINKS)
        warehouse.upsert(LINKS, [lien(378836, FINALE)])
        matchs = LiquipediaMatchPages(
            VALORANT,
            settings=settings,
            client=FakeMatchWiki(pages={FINALE: page_capturee()}).http_client(),
        )
        runner.run(matchs, warehouse, max_pages=10)
        matchs.close()


def _dbt(commande: str, chemin: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Lance dbt sur l'entrepôt indiqué, sans rien écrire dans le dépôt."""
    environnement = {
        **os.environ,
        "WAREHOUSE_PATH": str(chemin),
        "DBT_TARGET_PATH": str(tmp_path / "target"),
        "DBT_LOG_PATH": str(tmp_path / "logs"),
    }
    return subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", commande, "--no-partial-parse"],
        cwd=TRANSFORM,
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.fixture
def entrepot_peuple(tmp_path: Path, fake_api: FakeProMatchesApi, settings: Settings) -> Path:
    chemin = tmp_path / "essai.duckdb"
    _ingerer(chemin, fake_api, settings)
    return chemin


def test_dbt_construit_et_valide_toute_la_couche(entrepot_peuple: Path, tmp_path: Path) -> None:
    """`dbt build` enchaîne graine, modèles et tests : un seul verdict."""
    resultat = _dbt("build", entrepot_peuple, tmp_path)

    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert "ERROR=0" in resultat.stdout
    assert "Completed successfully" in resultat.stdout


def test_les_deux_echelles_de_tiers_se_rejoignent(
    entrepot_peuple: Path, tmp_path: Path
) -> None:
    """Le point de la couche : « S-Tier » et « 1 » deviennent un même rang."""
    assert _dbt("build", entrepot_peuple, tmp_path).returncode == 0

    with Warehouse.open(entrepot_peuple) as warehouse:
        rangs = warehouse.query(
            "SELECT discipline, tier_source, tier_rank FROM marts.dim_tournament "
            "WHERE tier_source IS NOT NULL ORDER BY tier_rank, discipline"
        )

    par_discipline = {(ligne[0], ligne[1]): ligne[2] for ligne in rangs}
    assert par_discipline[("counterstrike", "S-Tier")] == 1
    assert par_discipline[("dota2", "1")] == 1
    assert {rang for _, _, rang in rangs} <= {1, 2, 3, 4, 5}


def test_une_fin_anterieure_au_debut_ne_produit_pas_de_duree_negative(
    entrepot_peuple: Path, tmp_path: Path
) -> None:
    """La source se contredit parfois : la dimension le dit au lieu de calculer."""
    with Warehouse.open(entrepot_peuple) as warehouse:
        warehouse.query(
            "UPDATE raw.liquipedia_tournaments SET end_date = start_date - 10 "
            "WHERE start_date IS NOT NULL"
        )

    assert _dbt("build", entrepot_peuple, tmp_path).returncode == 0

    with Warehouse.open(entrepot_peuple) as warehouse:
        conflits = warehouse.query(
            "SELECT count(*), count(duration_days), count(end_date) "
            "FROM marts.dim_tournament WHERE has_date_conflict"
        )
    total, durees, fins = conflits[0]
    assert total > 0
    assert durees == 0  # aucune durée négative n'est inventée
    assert fins == 0  # la fin contredite est laissée inconnue


def test_un_tier_inconnu_fait_echouer_le_build(entrepot_peuple: Path, tmp_path: Path) -> None:
    """Un vocabulaire qui change doit se voir, pas disparaître dans des nuls.

    C'est l'intérêt du test `assert_tiers_tous_traduits` : sans lui, les
    tournois concernés sortiraient silencieusement de toute analyse par niveau.
    """
    assert _dbt("build", entrepot_peuple, tmp_path).returncode == 0
    with Warehouse.open(entrepot_peuple) as warehouse:
        warehouse.query(
            "UPDATE raw.liquipedia_tournaments SET tier = 'Z-Tier' WHERE wiki = 'counterstrike'"
        )

    resultat = _dbt("build", entrepot_peuple, tmp_path)

    assert resultat.returncode != 0
    assert "assert_tiers_tous_traduits" in resultat.stdout


# -- Valorant : une finale, de la page rendue aux tables d'analyse ---------------


def test_une_finale_se_deplie_en_cartes_joueurs_et_manches(
    entrepot_peuple: Path, tmp_path: Path
) -> None:
    """Quatre cartes, dix joueurs par carte, et autant de manches que de points."""
    assert _dbt("build", entrepot_peuple, tmp_path).returncode == 0

    with Warehouse.open(entrepot_peuple) as warehouse:
        cartes = warehouse.scalar("SELECT count(*) FROM marts.fct_map")
        joueurs = warehouse.scalar("SELECT count(*) FROM marts.fct_player_map")
        manches = warehouse.scalar("SELECT count(*) FROM marts.fct_round")
        points = warehouse.scalar("SELECT sum(rounds_played) FROM marts.fct_map")

    assert (cartes, joueurs) == (4, 40)
    assert manches == points == 86


def test_un_match_valorant_retrouve_son_tournoi_par_identifiant(
    entrepot_peuple: Path, tmp_path: Path
) -> None:
    """Le lien qui manquait côté Dota 2 : ici, la page de match nomme son tournoi."""
    assert _dbt("build", entrepot_peuple, tmp_path).returncode == 0

    with Warehouse.open(entrepot_peuple) as warehouse:
        rencontre = warehouse.query(
            "SELECT tournament_key, tier_rank, winner_team, side_1_maps, side_2_maps "
            "FROM marts.fct_series"
        )

    assert rencontre == [("liquipedia:valorant:378836", 1, "Team Heretics", 1, 3)]


def test_le_veto_dit_sur_la_carte_de_qui_on_a_gagne(
    entrepot_peuple: Path, tmp_path: Path
) -> None:
    assert _dbt("build", entrepot_peuple, tmp_path).returncode == 0

    with Warehouse.open(entrepot_peuple) as warehouse:
        choix = warehouse.query(
            "SELECT map_name, picked_by, winner_team FROM marts.fct_map ORDER BY map_number"
        )

    assert choix == [
        ("Icebox", "Leviatán", "Team Heretics"),
        ("Sunset", "Team Heretics", "Team Heretics"),
        ("Abyss", "Leviatán", "Leviatán"),
        ("Lotus", "Team Heretics", "Team Heretics"),
    ]


def test_chaque_manche_sait_ou_en_etait_le_score_et_qui_l_a_ouverte(
    entrepot_peuple: Path, tmp_path: Path
) -> None:
    assert _dbt("build", entrepot_peuple, tmp_path).returncode == 0

    with Warehouse.open(entrepot_peuple) as warehouse:
        derniere = warehouse.query(
            "SELECT side_1_score_before, side_2_score_before, winning_side, "
            "first_kill_side, first_kill_converted "
            "FROM marts.fct_round WHERE map_name = 'Icebox' ORDER BY round_number DESC LIMIT 1"
        )
        sans_camp = warehouse.scalar(
            "SELECT count(*) FROM marts.fct_round "
            "WHERE first_kill_player IS NOT NULL AND first_kill_side IS NULL"
        )

    assert derniere == [(7, 12, 2, 2, True)]
    assert sans_camp == 0  # chaque premier kill se rattache à un joueur de la carte


def test_les_cartes_valorant_rejoignent_les_matchs_dota_dans_la_table_commune(
    entrepot_peuple: Path, tmp_path: Path
) -> None:
    """La promesse du vocabulaire neutre : une union, pas une refonte."""
    assert _dbt("build", entrepot_peuple, tmp_path).returncode == 0

    with Warehouse.open(entrepot_peuple) as warehouse:
        par_discipline = dict(
            warehouse.query("SELECT discipline, count(*) FROM marts.fct_match GROUP BY 1")
        )

    assert par_discipline == {"dota2": 14, "valorant": 4}
