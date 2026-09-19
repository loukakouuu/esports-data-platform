"""Le découpage du wikitexte : là où une erreur silencieuse coûterait le plus cher.

Une infobox mal découpée ne lève rien — elle rend des valeurs fausses. D'où des
tests sur les cas qui piègent : imbrication, liens contenant une barre verticale,
modèle jamais refermé.
"""

from __future__ import annotations

import pytest

from ingestion.sources.wikitext import (
    find_template,
    parse_fields,
    strip_comments,
    strip_markup,
)
from tests.conftest import load_liquipedia_corpus

# -- Commentaires HTML ------------------------------------------------------


def test_un_commentaire_ferme_disparait() -> None:
    assert strip_comments("avant <!-- caché --> après") == "avant  après"


def test_un_commentaire_jamais_ferme_emporte_la_suite() -> None:
    """C'est ainsi que MediaWiki le rend : autant le lire pareil."""
    assert strip_comments("visible <!-- oubli\nsur\nplusieurs lignes") == "visible "


def test_une_barre_commentee_ne_coupe_pas_un_champ() -> None:
    """Le défaut que 25 000 tournois ont révélé : 340 pages en portaient un."""
    champs = parse_fields(
        "Infobox|liquipediatier=2<!-- discuté sur Discord|name=X -->|city=Oslo"
    )

    assert champs["liquipediatier"] == "2"
    assert champs["city"] == "Oslo"
    assert "name" not in champs


def test_un_commentaire_non_ferme_ne_colle_pas_au_tier() -> None:
    champs = parse_fields("Infobox|liquipediatier=C-Tier <!-- à revoir")

    assert champs["liquipediatier"] == "C-Tier"


def test_une_accolade_commentee_ne_trompe_pas_la_recherche_du_modele() -> None:
    corps = find_template(
        "<!-- {{Infobox league|name=leurre}} -->{{Infobox league|name=vrai}}", "Infobox league"
    )

    assert corps is not None
    assert parse_fields(corps)["name"] == "vrai"


# -- Retrouver le modèle ----------------------------------------------------


def test_le_modele_se_retrouve_par_son_nom() -> None:
    corps = find_template("texte {{Infobox league|name=Major}} suite", "Infobox league")

    assert corps == "Infobox league|name=Major"


def test_la_casse_du_nom_n_a_pas_d_importance() -> None:
    assert find_template("{{infobox LEAGUE|a=1}}", "Infobox league") is not None


def test_un_modele_absent_ne_rend_rien() -> None:
    assert find_template("{{Autre modèle|a=1}}", "Infobox league") is None


def test_un_modele_imbrique_ne_referme_pas_celui_qui_l_englobe() -> None:
    """Le piège : la première `}}` rencontrée n'est pas forcément la bonne."""
    corps = find_template("{{Infobox league|format={{Abbr/Bo3}}|name=X}}", "Infobox league")

    assert corps == "Infobox league|format={{Abbr/Bo3}}|name=X"


def test_un_modele_jamais_referme_ne_rend_rien() -> None:
    """Page en cours d'édition : mieux vaut rien que la moitié d'une infobox."""
    assert find_template("{{Infobox league|name=X", "Infobox league") is None


# -- Découper les champs ----------------------------------------------------


def test_les_champs_se_lisent_en_paires() -> None:
    champs = parse_fields("Infobox league|name=PGL Major|country=Denmark")

    assert champs == {"name": "PGL Major", "country": "Denmark"}


def test_les_cles_sont_ramenees_en_minuscules() -> None:
    """Liquipedia alterne `sdate` et `sDate` d'une page à l'autre."""
    assert parse_fields("Infobox|sDate=2024-03-17") == {"sdate": "2024-03-17"}


def test_une_barre_dans_un_modele_n_est_pas_un_separateur() -> None:
    champs = parse_fields("Infobox|previous=BLAST/2023{{!}}BLAST Paris|name=X")

    assert champs["previous"] == "BLAST/2023{{!}}BLAST Paris"
    assert champs["name"] == "X"


def test_une_barre_dans_un_lien_interne_n_est_pas_un_separateur() -> None:
    champs = parse_fields("Infobox|venue=[[Royal Arena|l'arène]]|city=Copenhagen")

    assert champs["venue"] == "[[Royal Arena|l'arène]]"
    assert champs["city"] == "Copenhagen"


def test_un_signe_egal_dans_la_valeur_est_conserve() -> None:
    champs = parse_fields("Infobox|web=http://x.fr/?a=1&b=2")

    assert champs["web"] == "http://x.fr/?a=1&b=2"


def test_un_parametre_sans_nom_est_ignore() -> None:
    """On ne saurait pas quoi en faire : mieux vaut l'écarter que le deviner."""
    assert parse_fields("Infobox|positionnel|name=X") == {"name": "X"}


def test_plusieurs_champs_sur_une_meme_ligne_sont_lus() -> None:
    """Le wiki Dota 2 écrit parfois deux champs sur la même ligne."""
    champs = parse_fields("Infobox|venuelink=https://x.fr |venue=Royal Arena")

    assert champs["venuelink"] == "https://x.fr"
    assert champs["venue"] == "Royal Arena"


# -- Nettoyer une valeur ----------------------------------------------------


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ("[https://www.royalarena.dk Royal Arena]", "Royal Arena"),
        ("[[Copenhagen]]", "Copenhagen"),
        ("[[Danemark|Copenhague]]", "Copenhague"),
        ("Intel<br>Razer", "Intel Razer"),
        ("Intel<br />Razer", "Intel Razer"),
        ("'''Group Stage'''", "Group Stage"),
        ("Double-Elimination ({{Abbr/Bo1}})", "Double-Elimination ()"),
        ("{{Flag/dk}} Denmark", "Denmark"),
        ("texte <!-- commentaire --> suite", "texte suite"),
        ("  espaces    multiples  ", "espaces multiples"),
        ("", ""),
    ],
)
def test_le_balisage_decoratif_est_retire(brut: str, attendu: str) -> None:
    assert strip_markup(brut) == attendu


def test_les_modeles_imbriques_sont_retires_de_proche_en_proche() -> None:
    assert strip_markup("a {{x|{{y}}}} b") == "a b"


# -- Sur du wikitexte réel --------------------------------------------------


def test_chaque_page_capturee_livre_son_infobox() -> None:
    """Sept pages réelles, deux wikis : aucune ne doit échapper au découpage."""
    pages = load_liquipedia_corpus()
    assert len(pages) == 7

    for page in pages:
        corps = find_template(page["wikitext"], "Infobox league")
        assert corps is not None, page["title"]
        champs = parse_fields(corps)
        assert champs.get("name"), page["title"]
        assert len(champs) >= 20, page["title"]


def test_les_valeurs_lues_sont_celles_de_la_page() -> None:
    page = next(p for p in load_liquipedia_corpus() if p["title"] == "PGL/2024/Copenhagen")

    corps = find_template(page["wikitext"], "Infobox league")
    assert corps is not None
    champs = parse_fields(corps)

    assert champs["name"] == "PGL Major Copenhagen 2024"
    assert champs["liquipediatier"] == "S-Tier"
    assert champs["prizepoolusd"] == "1,250,000"
    assert champs["sdate"] == "2024-03-17"
    assert strip_markup(champs["venue"]) == "Royal Arena"
