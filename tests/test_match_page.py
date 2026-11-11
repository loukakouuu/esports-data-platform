"""Lecture d'une page de match rendue, sur une vraie finale.

L'instantané est la finale du tableau bas de VALORANT Champions 2024 — Leviatán
contre Team Heretics, quatre cartes dont une en prolongation. Le parseur n'a que
le HTML pour lui : chaque nombre lu ici se recoupe avec un autre.
"""

from __future__ import annotations

import gzip
from typing import Any

import pytest

from ingestion.sources.match_page import compact_html, parse_match_page
from tests.conftest import FIXTURES


def page_capturee() -> str:
    return gzip.decompress((FIXTURES / "liquipedia_match_page.html.gz").read_bytes()).decode(
        "utf-8"
    )


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return parse_match_page(page_capturee())


# -- En-tête ----------------------------------------------------------------


def test_l_entete_dit_qui_joue_quand_et_ou(document: dict[str, Any]) -> None:
    assert document["status"] == "finished"
    assert document["tournament_page"] == "VCT/2024/Champions"
    assert [team["page"] for team in document["teams"]] == ["Leviatán", "Team Heretics"]
    assert [team["short"] for team in document["teams"]] == ["LEV", "TH"]
    assert document["score"] == [1, 3]


def test_l_instant_du_match_est_lu_en_utc_sans_passer_par_le_fuseau_affiche(
    document: dict[str, Any],
) -> None:
    """La page affiche « 16:00 KST » ; l'attribut porte l'instant exact."""
    assert document["played_at_epoch"] == 1724482800  # 2024-08-24 07:00 UTC


def test_le_veto_garde_son_ordre_et_son_auteur(document: dict[str, Any]) -> None:
    veto = document["veto"]

    assert [(v["action"], v["map"]) for v in veto[:3]] == [
        ("ban", "Bind"),
        ("ban", "Ascent"),
        ("pick", "Icebox"),
    ]
    assert veto[0]["team"] == "Leviatán"
    assert veto[-1] == {"order": 7, "action": "decider", "map": "Haven", "team": None}


# -- Cartes -----------------------------------------------------------------


def test_chaque_carte_jouee_est_lue_et_l_onglet_d_ensemble_ne_l_est_pas(
    document: dict[str, Any],
) -> None:
    cartes = document["maps"]

    assert [(c["number"], c["map"], c["score"]) for c in cartes] == [
        (1, "Icebox", [7, 13]),
        (2, "Sunset", [13, 15]),
        (3, "Abyss", [13, 9]),
        (4, "Lotus", [3, 13]),
    ]


def test_les_manches_recomptees_retombent_sur_le_score_affiche(
    document: dict[str, Any],
) -> None:
    """Deux lectures indépendantes de la page doivent s'accorder."""
    for carte in document["maps"]:
        gagnees = [sum(1 for m in carte["rounds"] if m["winner"] == camp) for camp in (1, 2)]
        assert gagnees == carte["score"], carte["map"]


def test_les_mi_temps_s_additionnent_au_score_prolongation_comprise(
    document: dict[str, Any],
) -> None:
    for carte in document["maps"]:
        for camp in (0, 1):
            assert sum(m["rounds"] for m in carte["halves"][camp]) == carte["score"][camp]
    sunset = document["maps"][1]
    assert len(sunset["halves"][0]) == 4  # deux mi-temps, puis la prolongation


def test_les_camps_s_inversent_a_la_mi_temps(document: dict[str, Any]) -> None:
    icebox = document["maps"][0]

    assert [m["side"] for m in icebox["halves"][0]] == ["atk", "def"]
    assert [m["side"] for m in icebox["halves"][1]] == ["def", "atk"]


# -- Joueurs ----------------------------------------------------------------


def test_dix_joueurs_par_carte_cinq_par_camp(document: dict[str, Any]) -> None:
    for carte in document["maps"]:
        assert [j["side"] for j in carte["players"]].count(1) == 5
        assert [j["side"] for j in carte["players"]].count(2) == 5


def test_la_ligne_d_un_joueur_porte_son_agent_et_ses_statistiques(
    document: dict[str, Any],
) -> None:
    kingg = next(j for j in document["maps"][0]["players"] if j["page"] == "KiNgg")

    assert kingg == {
        "side": 1,
        "page": "KiNgg",
        "name": "kiNgg",
        "agent": "Viper",
        "acs": 215.0,
        "kills": 15,
        "deaths": 14,
        "assists": 5,
        "kast": 75.0,
        "adr": 189.0,
        "headshot_pct": 34.2,
        "first_kills": 3,
        "first_deaths": 2,
    }


def test_les_premiers_kills_des_joueurs_font_ceux_de_l_equipe(
    document: dict[str, Any],
) -> None:
    """Statistique d'équipe et statistiques de joueurs : deux blocs, un seul total."""
    for carte in document["maps"]:
        for camp in (1, 2):
            des_joueurs = sum(j["first_kills"] for j in carte["players"] if j["side"] == camp)
            assert des_joueurs == int(carte["team_stats"]["first_kills"][camp - 1])


def test_les_morts_d_un_camp_sont_les_kills_de_l_autre_a_peu_de_chose_pres(
    document: dict[str, Any],
) -> None:
    """Pas d'égalité stricte — le spike tue aussi — mais jamais d'écart béant."""
    for carte in document["maps"]:
        kills = sum(j["kills"] for j in carte["players"] if j["side"] == 1)
        morts_adverses = sum(j["deaths"] for j in carte["players"] if j["side"] == 2)
        assert abs(kills - morts_adverses) <= 3


# -- Manches ----------------------------------------------------------------


def test_une_manche_dit_qui_gagne_de_quel_camp_et_comment(document: dict[str, Any]) -> None:
    premiere = document["maps"][0]["rounds"][0]

    assert premiere == {
        "number": 1,
        "winner": 1,
        "winner_side": "atk",
        "outcome": "Spike detonated",
        "first_kill": "MiniBoo",
        "clutch": "C0M",
    }


def test_une_manche_sans_clutch_n_en_invente_pas(document: dict[str, Any]) -> None:
    seconde = document["maps"][0]["rounds"][1]

    assert "clutch" not in seconde
    assert seconde["first_kill"] == "Mazino"


def test_les_issues_de_manche_sont_celles_du_jeu(document: dict[str, Any]) -> None:
    issues = {m["outcome"] for c in document["maps"] for m in c["rounds"]}

    assert issues == {"Spike detonated", "Enemy eliminated", "Spike defused", "Timer expired"}


def test_les_clutchs_des_manches_font_ceux_de_l_equipe(document: dict[str, Any]) -> None:
    for carte in document["maps"]:
        attendus = sum(int(valeur) for valeur in carte["team_stats"]["clutches"])
        assert sum(1 for m in carte["rounds"] if "clutch" in m) == attendus


# -- Robustesse -------------------------------------------------------------


def test_une_page_sans_bloc_de_match_le_dit_au_lieu_d_echouer() -> None:
    document = parse_match_page("<div class='mw-parser-output'><p>Rien ici.</p></div>")

    assert document["status"] == "unparsed"
    assert document["maps"] == []
    assert compact_html("<p>Rien ici.</p>") is None


def test_la_page_allegee_se_relit_a_l_identique(document: dict[str, Any]) -> None:
    """C'est ce qui est stocké : il faut pouvoir en retirer le même document."""
    allegee = compact_html(page_capturee())

    assert allegee is not None
    assert parse_match_page(allegee) == document
    assert 'src="' not in allegee
