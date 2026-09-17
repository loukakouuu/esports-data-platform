"""La source Liquipedia : deux appels par page, wikitexte normalisé, balayage repris."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from ingestion.core import quality, runner
from ingestion.core.config import Settings
from ingestion.core.source import SourceError
from ingestion.core.state import Mode, StopReason
from ingestion.core.warehouse import Warehouse
from ingestion.sources.liquipedia import (
    DOTA2,
    TABLE,
    LiquipediaTournaments,
)
from tests.conftest import FakeMediaWikiApi, load_liquipedia_corpus

TABLE_NAME = TABLE.qualified_name


def page_capturee(titre: str) -> dict[str, Any]:
    return next(page for page in load_liquipedia_corpus() if page["title"] == titre)


def enregistrement(titre: str) -> dict[str, Any]:
    """Un enregistrement tel que `fetch` le compose : listing plus wikitexte."""
    page = page_capturee(titre)
    return {
        "pageid": page["pageid"],
        "title": page["title"],
        "wikitext": page["wikitext"],
        "revised_at": page["revised_at"],
    }


# -- Les deux appels --------------------------------------------------------


def test_une_page_coute_deux_appels_et_pas_un_par_tournoi(
    liquipedia: LiquipediaTournaments, fake_wiki: FakeMediaWikiApi
) -> None:
    """C'est tout l'intérêt du lot : une requête de titres, une de contenus."""
    page = liquipedia.fetch(None)

    assert len(page.records) == 3
    assert len(fake_wiki.requests) == 2
    assert fake_wiki.requests[0].url.params["list"] == "categorymembers"
    assert fake_wiki.requests[1].url.params["prop"] == "revisions"


def test_seule_la_section_d_en_tete_est_demandee(
    liquipedia: LiquipediaTournaments, fake_wiki: FakeMediaWikiApi
) -> None:
    """85 fois moins à transférer pour la même infobox."""
    liquipedia.fetch(None)

    assert fake_wiki.requests[1].url.params["rvsection"] == "0"


def test_les_brouillons_d_utilisateurs_sont_ecartes_des_la_requete(
    liquipedia: LiquipediaTournaments, fake_wiki: FakeMediaWikiApi
) -> None:
    liquipedia.fetch(None)

    assert fake_wiki.requests[0].url.params["cmnamespace"] == "0"


def test_le_jeton_de_continuation_est_rendu_tel_quel(
    liquipedia: LiquipediaTournaments,
) -> None:
    page = liquipedia.fetch(None)

    assert page.next_cursor == "page|3"


def test_le_jeton_recu_repart_avec_la_requete_suivante(
    liquipedia: LiquipediaTournaments, fake_wiki: FakeMediaWikiApi
) -> None:
    premiere = liquipedia.fetch(None)
    assert premiere.next_cursor is not None

    suite = liquipedia.fetch(premiere.next_cursor)

    assert fake_wiki.requests[2].url.params["cmcontinue"] == premiere.next_cursor
    titres_vus = {record["title"] for record in premiere.records}
    assert not titres_vus & {record["title"] for record in suite.records}


def test_la_fin_du_balayage_ne_rend_plus_de_jeton(
    liquipedia: LiquipediaTournaments,
) -> None:
    derniere = liquipedia.fetch("page|3")

    assert len(derniere.records) == 1
    assert derniere.next_cursor is None


def test_une_categorie_vide_rend_une_page_vide(
    liquipedia: LiquipediaTournaments, fake_wiki: FakeMediaWikiApi
) -> None:
    fake_wiki.pages = []

    page = liquipedia.fetch(None)

    assert page.records == ()
    assert page.next_cursor is None
    assert len(fake_wiki.requests) == 1  # inutile de demander des contenus


def test_une_page_disparue_entre_les_deux_appels_n_arrete_pas_le_lot(
    liquipedia: LiquipediaTournaments, fake_wiki: FakeMediaWikiApi
) -> None:
    """Une page supprimée entre le listing et la lecture : les autres passent."""
    fake_wiki.queued_responses.append(
        httpx.Response(
            200,
            json={
                "query": {
                    "categorymembers": [
                        {"pageid": 999_999, "ns": 0, "title": "Page effacée"},
                        {
                            "pageid": page_capturee("121 Minor")["pageid"],
                            "ns": 0,
                            "title": "121 Minor",
                        },
                    ]
                }
            },
        )
    )

    page = liquipedia.fetch(None)

    assert len(page.records) == 2
    effacee = next(r for r in page.records if r["pageid"] == 999_999)
    assert "wikitext" not in effacee


def test_une_erreur_annoncee_dans_le_corps_est_levee(
    liquipedia: LiquipediaTournaments, fake_wiki: FakeMediaWikiApi
) -> None:
    """MediaWiki répond 200 même quand il refuse : l'erreur est dans le corps."""
    fake_wiki.queued_responses.append(
        httpx.Response(200, json={"error": {"code": "readapidenied", "info": "refusé"}})
    )

    with pytest.raises(SourceError, match="readapidenied"):
        liquipedia.fetch(None)


# -- Normalisation ----------------------------------------------------------


def test_une_infobox_complete_devient_une_ligne(liquipedia: LiquipediaTournaments) -> None:
    ligne = liquipedia.normalize(enregistrement("PGL/2024/Copenhagen"))

    assert ligne["wiki"] == "counterstrike"
    assert ligne["name"] == "PGL Major Copenhagen 2024"
    assert ligne["organizer"] == "PGL"
    assert ligne["tier"] == "S-Tier"
    assert ligne["country"] == "Denmark"
    assert ligne["city"] == "Copenhagen"
    assert ligne["start_date"] == date(2024, 3, 17)
    assert ligne["end_date"] == date(2024, 3, 31)
    assert ligne["prize_pool_usd"] == 1_250_000.0
    assert ligne["team_count"] == 24
    assert ligne["setting"] == "Offline"


def test_une_ligne_normalisee_epouse_exactement_la_table(
    liquipedia: LiquipediaTournaments,
) -> None:
    ligne = liquipedia.normalize(enregistrement("PGL/2024/Copenhagen"))

    assert set(ligne) == set(TABLE.column_names)
    TABLE.row_to_tuple(ligne)


def test_l_horodatage_de_revision_est_conserve(liquipedia: LiquipediaTournaments) -> None:
    """De quoi repérer plus tard ce qui a changé sans tout relire."""
    ligne = liquipedia.normalize(enregistrement("PGL/2024/Copenhagen"))

    revision = ligne["revised_at"]
    assert isinstance(revision, datetime)
    assert revision.tzinfo is not None


def test_le_balisage_decoratif_ne_passe_pas_dans_la_table(
    liquipedia: LiquipediaTournaments,
) -> None:
    ligne = liquipedia.normalize(enregistrement("2000 CPL Europe Cologne"))

    assert ligne["city"] == "Cologne"
    for valeur in (ligne["organizer"], ligne["country"], ligne["name"]):
        assert "[" not in str(valeur) and "{{" not in str(valeur)


def test_l_infobox_brute_est_conservee_entiere(liquipedia: LiquipediaTournaments) -> None:
    """Les colonnes retenues ne sont qu'une lecture parmi d'autres."""
    ligne = liquipedia.normalize(enregistrement("PGL/2024/Copenhagen"))

    infobox = json.loads(ligne["infobox"])
    assert infobox["liquipediatier"] == "S-Tier"
    assert "publishertier" in infobox  # champ non repris en colonne
    assert len(infobox) > 20


def test_une_page_sans_infobox_reste_une_ligne_valable(
    liquipedia: LiquipediaTournaments,
) -> None:
    """Mieux vaut une ligne vide qu'une page perdue : on saura qu'elle existe."""
    ligne = liquipedia.normalize(
        {"pageid": 42, "title": "Page sans infobox", "wikitext": "{{Autre}}\ntexte"}
    )

    assert ligne["page_id"] == 42
    assert ligne["page_title"] == "Page sans infobox"
    assert ligne["name"] is None
    assert json.loads(ligne["infobox"]) == {}


def test_une_page_absente_du_second_appel_reste_une_ligne(
    liquipedia: LiquipediaTournaments,
) -> None:
    ligne = liquipedia.normalize({"pageid": 7, "title": "Effacée"})

    assert ligne["page_id"] == 7
    assert ligne["start_date"] is None


def test_une_date_incomplete_reste_inconnue(liquipedia: LiquipediaTournaments) -> None:
    """« 2024-03-?? » ne devient pas le 1er mars."""
    ligne = liquipedia.normalize(
        {"pageid": 1, "title": "T", "wikitext": "{{Infobox league|sdate=2024-03-??|edate=}}"}
    )

    assert ligne["start_date"] is None
    assert ligne["end_date"] is None


def test_une_dotation_transclue_n_est_pas_devinee(liquipedia: LiquipediaTournaments) -> None:
    ligne = liquipedia.normalize(
        {"pageid": 1, "title": "T", "wikitext": "{{Infobox league|prizepoolusd={{:TI/Prize}}}}"}
    )

    assert ligne["prize_pool_usd"] is None


def test_le_vocabulaire_des_tiers_est_rendu_tel_quel(settings: Settings) -> None:
    """Les wikis ne classent pas pareil : « S-Tier » d'un côté, « 1 » de l'autre.

    Les réconcilier est le travail de la transformation, pas celui de la collecte.
    """
    pages = load_liquipedia_corpus("dota2")
    wiki = FakeMediaWikiApi(pages)
    source = LiquipediaTournaments(DOTA2, settings=settings, client=wiki.http_client())

    tiers = {
        source.normalize(
            {"pageid": p["pageid"], "title": p["title"], "wikitext": p["wikitext"]}
        )["tier"]
        for p in pages
    }

    assert tiers == {"1"}
    source.close()


# -- Balayage de bout en bout ----------------------------------------------


def test_le_balayage_parcourt_toute_la_categorie(
    liquipedia: LiquipediaTournaments, warehouse: Warehouse
) -> None:
    report = runner.run(liquipedia, warehouse, max_pages=10)

    assert report.stop_reason is StopReason.EXHAUSTED
    assert (report.pages, report.records, report.inserted) == (2, 4, 4)
    assert warehouse.count(TABLE_NAME) == 4


def test_un_balayage_termine_remet_la_frontiere_a_zero(
    liquipedia: LiquipediaTournaments, warehouse: Warehouse
) -> None:
    """Plus rien à poursuivre : la reprise suivante repartira du début."""
    runner.run(liquipedia, warehouse, max_pages=10)

    state = warehouse.load_state(liquipedia.key)
    assert state is not None
    assert state.backfill_cursor is None
    assert state.high_watermark is None  # un balayage n'a pas de sommet


def test_un_balayage_borne_reprend_au_jeton(
    liquipedia: LiquipediaTournaments, warehouse: Warehouse, fake_wiki: FakeMediaWikiApi
) -> None:
    premiere = runner.run(liquipedia, warehouse, max_pages=1)
    assert premiere.stop_reason is StopReason.PAGE_LIMIT
    etat = warehouse.load_state(liquipedia.key)
    assert etat is not None and etat.backfill_cursor == "page|3"
    appels = len(fake_wiki.requests)

    suite = runner.run(liquipedia, warehouse, mode=Mode.BACKFILL, max_pages=10)

    assert fake_wiki.requests[appels].url.params["cmcontinue"] == "page|3"
    assert suite.inserted == 1
    assert warehouse.count(TABLE_NAME) == 4


def test_relancer_le_balayage_ne_duplique_rien(
    liquipedia: LiquipediaTournaments, warehouse: Warehouse
) -> None:
    runner.run(liquipedia, warehouse, max_pages=10)

    second = runner.run(liquipedia, warehouse, max_pages=10)

    assert warehouse.count(TABLE_NAME) == 4
    assert (second.inserted, second.updated) == (0, 4)


def test_un_balayage_ne_signale_jamais_de_lacune(
    liquipedia: LiquipediaTournaments, warehouse: Warehouse
) -> None:
    """La notion n'a pas de sens sans ordre : on ne prétend pas le contraire."""
    runner.run(liquipedia, warehouse, max_pages=1)

    assert not runner.run(liquipedia, warehouse, max_pages=1).gap_suspected


def test_les_deux_wikis_cohabitent_dans_la_meme_table(
    liquipedia: LiquipediaTournaments, warehouse: Warehouse, settings: Settings
) -> None:
    """Même ressource, deux disciplines : c'est la clé composite qui les sépare."""
    runner.run(liquipedia, warehouse, max_pages=10)
    dota = LiquipediaTournaments(
        DOTA2,
        settings=settings,
        client=FakeMediaWikiApi(load_liquipedia_corpus("dota2")).http_client(),
        batch_size=3,
    )

    runner.run(dota, warehouse, max_pages=10)

    par_wiki = dict(warehouse.query(f"SELECT wiki, count(*) FROM {TABLE_NAME} GROUP BY 1"))
    assert par_wiki == {"counterstrike": 4, "dota2": 3}
    assert warehouse.load_state(liquipedia.key) != warehouse.load_state(dota.key)
    dota.close()


# -- Tests de données -------------------------------------------------------


def test_les_tournois_collectes_satisfont_les_attentes_bloquantes(
    liquipedia: LiquipediaTournaments, warehouse: Warehouse
) -> None:
    runner.run(liquipedia, warehouse, max_pages=10)

    results = quality.run_checks(warehouse, liquipedia.checks)

    assert not quality.has_blocking_failure(results)


def test_un_tournoi_qui_finit_avant_de_commencer_est_attrape(
    liquipedia: LiquipediaTournaments, warehouse: Warehouse
) -> None:
    runner.run(liquipedia, warehouse, max_pages=10)
    warehouse.query(f"UPDATE {TABLE_NAME} SET end_date = start_date - 1 WHERE true")

    resultats = {r.check.name: r for r in quality.run_checks(warehouse, liquipedia.checks)}

    assert resultats["dates_ordonnees"].is_blocking


def test_une_date_anterieure_aux_premiers_tournois_est_attrapee(
    liquipedia: LiquipediaTournaments, warehouse: Warehouse
) -> None:
    runner.run(liquipedia, warehouse, max_pages=10)
    warehouse.query(f"UPDATE {TABLE_NAME} SET start_date = DATE '1970-01-01' WHERE true")

    resultats = {r.check.name: r for r in quality.run_checks(warehouse, liquipedia.checks)}

    assert resultats["dates_plausibles"].is_blocking


def test_un_agent_sans_contact_est_signale(
    caplog: pytest.LogCaptureFixture, fake_wiki: FakeMediaWikiApi, settings: Settings
) -> None:
    """Les conditions d'utilisation demandent un contact ; son absence doit se dire."""
    with caplog.at_level("WARNING"):
        source = LiquipediaTournaments(DOTA2, settings=settings, client=fake_wiki.http_client())

    assert "LIQUIPEDIA_USER_AGENT" in caplog.text
    assert source.user_agent == settings.user_agent
    source.close()


def test_un_contact_configure_est_retenu(fake_wiki: FakeMediaWikiApi) -> None:
    """Le contact vit dans l'environnement, jamais dans le dépôt."""
    avec_contact = Settings(
        warehouse_path=Path("mémoire"),
        user_agent="défaut sans contact",
        liquipedia_user_agent="esports-data-platform/0.1 (contact@exemple.fr)",
    )

    source = LiquipediaTournaments(DOTA2, settings=avec_contact, client=fake_wiki.http_client())

    assert source.user_agent == "esports-data-platform/0.1 (contact@exemple.fr)"
    source.close()


def test_l_horodatage_utc_est_relu_correctement(liquipedia: LiquipediaTournaments) -> None:
    ligne = liquipedia.normalize(
        {
            "pageid": 1,
            "title": "T",
            "wikitext": "{{Infobox league|name=X}}",
            "revised_at": "2026-07-19T00:57:27Z",
        }
    )

    assert ligne["revised_at"] == datetime(2026, 7, 19, 0, 57, 27, tzinfo=UTC)
