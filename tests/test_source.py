"""Arithmétique de l'état, sur une source paginée par identifiant décroissant.

C'est le point délicat de l'ingestion incrémentale : savoir jusqu'où on est
descendu, et si ce qu'on vient de parcourir touche ce qu'on connaissait déjà.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from ingestion.core.source import DescendingIdSource, Page
from ingestion.core.state import IngestionState, Mode, SourceKey, Walk
from ingestion.core.table import Column, TableSpec

KEY = SourceKey(name="fournisseur", discipline="dota2", resource="matchs")
SPEC = TableSpec(
    schema="raw",
    name="matchs",
    columns=(Column("match_id", "BIGINT"), Column("vainqueur", "VARCHAR")),
    primary_key="match_id",
)


class SourceMuette(DescendingIdSource):
    """Une source réduite à sa gestion d'état : elle ne va chercher nulle part."""

    def __init__(self, id_column: str = "match_id") -> None:
        super().__init__(key=KEY, table=SPEC, id_column=id_column)

    def fetch(self, cursor: str | None) -> Page:
        raise NotImplementedError

    def normalize(self, record: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


@pytest.fixture
def source() -> SourceMuette:
    return SourceMuette()


PAGE_VIDE = Page(records=(), next_cursor=None)
"""Les sources ordonnées lisent leur frontière dans les lignes : la page ne
leur sert à rien, et les tests n'ont donc pas à la garnir."""


def page(*identifiers: int) -> Sequence[Mapping[str, Any]]:
    return [{"match_id": identifier, "vainqueur": "radiant"} for identifier in identifiers]


def test_une_colonne_de_curseur_absente_de_la_table_est_refusee() -> None:
    with pytest.raises(ValueError, match="absente"):
        SourceMuette(id_column="inconnue")


# -- Point de départ --------------------------------------------------------


def test_le_rattrapage_repart_toujours_du_sommet(source: SourceMuette) -> None:
    state = IngestionState(key=KEY, high_watermark="900", backfill_cursor="800")

    assert source.start_cursor(Mode.CATCH_UP, state) is None


def test_le_backfill_reprend_sous_la_frontiere_basse(source: SourceMuette) -> None:
    state = IngestionState(key=KEY, high_watermark="900", backfill_cursor="800")

    assert source.start_cursor(Mode.BACKFILL, state) == "800"


def test_un_backfill_sans_rien_de_connu_part_du_sommet(source: SourceMuette) -> None:
    assert source.start_cursor(Mode.BACKFILL, IngestionState.empty(KEY)) is None


# -- Ce que la descente a couvert -------------------------------------------


def test_la_descente_agrege_les_bornes_page_apres_page(source: SourceMuette) -> None:
    before = IngestionState.empty(KEY)

    walk = source.extend(Walk(), PAGE_VIDE, page(900, 880), before)
    walk = source.extend(walk, PAGE_VIDE, page(870, 850), before)

    assert (walk.summit, walk.frontier, walk.records) == ("900", "850", 4)
    assert not walk.reached_known


def test_la_descente_signale_le_moment_ou_elle_rejoint_du_connu(source: SourceMuette) -> None:
    before = IngestionState(key=KEY, high_watermark="880", backfill_cursor="800")

    walk = source.extend(Walk(), PAGE_VIDE, page(920, 900), before)
    assert not walk.reached_known

    walk = source.extend(walk, PAGE_VIDE, page(890, 870), before)
    assert walk.reached_known


def test_la_jonction_une_fois_faite_ne_se_defait_pas(source: SourceMuette) -> None:
    """Les pages suivantes descendent sous le connu : elles ne l'annulent pas."""
    before = IngestionState(key=KEY, high_watermark="880", backfill_cursor="800")

    walk = source.extend(Walk(), PAGE_VIDE, page(890, 870), before)
    walk = source.extend(walk, PAGE_VIDE, page(700, 690), before)

    assert walk.reached_known


def test_une_page_vide_laisse_la_descente_inchangee(source: SourceMuette) -> None:
    walk = Walk(frontier="800", summit="900", records=2)

    assert source.extend(walk, PAGE_VIDE, [], IngestionState.empty(KEY)) == walk


# -- Nouvel état ------------------------------------------------------------


def test_la_premiere_execution_pose_les_deux_bornes(source: SourceMuette) -> None:
    before = IngestionState.empty(KEY)
    walk = source.extend(Walk(), PAGE_VIDE, page(900, 850), before)

    after = source.advance(before=before, walk=walk, mode=Mode.CATCH_UP)

    assert (after.high_watermark, after.backfill_cursor) == ("900", "850")
    assert after.records_seen == 2
    assert after.last_run_at is not None


def test_un_rattrapage_qui_rejoint_le_connu_conserve_la_frontiere_basse(
    source: SourceMuette,
) -> None:
    """Les deux intervalles se rejoignent : le contigu court désormais de 800 à 950."""
    before = IngestionState(key=KEY, high_watermark="880", backfill_cursor="800")
    walk = source.extend(Walk(), PAGE_VIDE, page(950, 870), before)

    after = source.advance(before=before, walk=walk, mode=Mode.CATCH_UP)

    assert (after.high_watermark, after.backfill_cursor) == ("950", "800")


def test_un_rattrapage_arrete_trop_tot_redescend_la_frontiere_a_son_ilot(
    source: SourceMuette,
) -> None:
    """Sans jonction, l'intervalle contigu redémarre plus haut.

    Rien n'est perdu — les lignes d'avant restent écrites — mais l'état dit la
    vérité : la zone sautée reste à reprendre par un backfill.
    """
    before = IngestionState(key=KEY, high_watermark="880", backfill_cursor="800")
    walk = source.extend(Walk(), PAGE_VIDE, page(2000, 1900), before)

    after = source.advance(before=before, walk=walk, mode=Mode.CATCH_UP)

    assert (after.high_watermark, after.backfill_cursor) == ("2000", "1900")


def test_un_ilot_qui_finit_par_rejoindre_le_connu_retrouve_l_ancienne_frontiere(
    source: SourceMuette,
) -> None:
    """Chaque page se compare à l'état d'avant l'exécution, jamais à l'état courant."""
    before = IngestionState(key=KEY, high_watermark="880", backfill_cursor="800")

    walk = source.extend(Walk(), PAGE_VIDE, page(2000, 1900), before)
    ilot = source.advance(before=before, walk=walk, mode=Mode.CATCH_UP)
    assert ilot.backfill_cursor == "1900"

    walk = source.extend(walk, PAGE_VIDE, page(1000, 870), before)
    after = source.advance(before=before, walk=walk, mode=Mode.CATCH_UP)

    assert (after.high_watermark, after.backfill_cursor) == ("2000", "800")


def test_un_backfill_ne_touche_pas_au_sommet(source: SourceMuette) -> None:
    before = IngestionState(key=KEY, high_watermark="900", backfill_cursor="800")
    walk = source.extend(Walk(), PAGE_VIDE, page(790, 700), before)

    after = source.advance(before=before, walk=walk, mode=Mode.BACKFILL)

    assert (after.high_watermark, after.backfill_cursor) == ("900", "700")


def test_une_execution_sans_page_laisse_l_etat_intact(source: SourceMuette) -> None:
    before = IngestionState(key=KEY, high_watermark="900", backfill_cursor="800")

    assert source.advance(before=before, walk=Walk(), mode=Mode.CATCH_UP) == before


def test_les_enregistrements_vus_s_accumulent_d_une_execution_a_l_autre(
    source: SourceMuette,
) -> None:
    before = IngestionState(
        key=KEY, high_watermark="900", backfill_cursor="800", records_seen=40
    )
    walk = source.extend(Walk(), PAGE_VIDE, page(790, 700), before)

    after = source.advance(before=before, walk=walk, mode=Mode.BACKFILL)

    assert after.records_seen == 42
