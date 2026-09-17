"""L'entrepôt : déclaration des tables, écriture idempotente, état persistant."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ingestion.core.state import (
    IngestionState,
    Mode,
    RunReport,
    SourceKey,
    StopReason,
)
from ingestion.core.table import Column, TableSpec
from ingestion.core.warehouse import RUNS_TABLE, STATE_TABLE, Warehouse

KEY = SourceKey(name="fournisseur", discipline="dota2", resource="matchs")

SPEC = TableSpec(
    schema="raw",
    name="matchs",
    columns=(
        Column("match_id", "BIGINT NOT NULL"),
        Column("vainqueur", "VARCHAR"),
        Column("ingested_at", "TIMESTAMPTZ NOT NULL"),
    ),
    primary_key="match_id",
    write_once=frozenset({"ingested_at"}),
)

PREMIERE_ECRITURE = datetime(2026, 1, 1, tzinfo=UTC)
SECONDE_ECRITURE = datetime(2026, 6, 1, tzinfo=UTC)


def ligne(match_id: int, vainqueur: str, at: datetime = PREMIERE_ECRITURE) -> dict[str, object]:
    return {"match_id": match_id, "vainqueur": vainqueur, "ingested_at": at}


# -- Déclaration ------------------------------------------------------------


def test_une_cle_absente_des_colonnes_est_refusee() -> None:
    with pytest.raises(ValueError, match="absente"):
        TableSpec(
            schema="raw",
            name="t",
            columns=(Column("a", "INTEGER"),),
            primary_key="b",
        )


def test_les_colonnes_figees_sortent_des_mises_a_jour() -> None:
    assert SPEC.updatable_columns == ("vainqueur",)
    assert "ingested_at = EXCLUDED.ingested_at" not in SPEC.upsert_sql()


def test_une_table_sans_colonne_reinscriptible_laisse_les_lignes_intactes() -> None:
    spec = TableSpec(
        schema="raw",
        name="t",
        columns=(Column("id", "BIGINT"),),
        primary_key="id",
    )
    assert "DO NOTHING" in spec.upsert_sql()


def test_une_ligne_qui_s_ecarte_du_schema_est_arretee_avant_l_ecriture() -> None:
    with pytest.raises(ValueError, match="colonnes manquantes"):
        SPEC.row_to_tuple({"match_id": 1, "ingested_at": PREMIERE_ECRITURE})
    with pytest.raises(ValueError, match="colonnes inattendues"):
        SPEC.row_to_tuple({**ligne(1, "radiant"), "champ_oublie": 3})


# -- Écriture ---------------------------------------------------------------


def test_ecrire_deux_fois_le_meme_lot_ne_duplique_rien(warehouse: Warehouse) -> None:
    warehouse.ensure_table(SPEC)
    lot = [ligne(1, "radiant"), ligne(2, "dire")]

    premier = warehouse.upsert(SPEC, lot)
    second = warehouse.upsert(SPEC, lot)

    assert (premier.inserted, premier.updated) == (2, 0)
    assert (second.inserted, second.updated) == (0, 2)
    assert warehouse.count(SPEC.qualified_name) == 2


def test_un_doublon_interne_au_lot_est_reduit_avant_ecriture(warehouse: Warehouse) -> None:
    """DuckDB refuse de mettre à jour deux fois la même ligne dans un seul INSERT."""
    warehouse.ensure_table(SPEC)

    result = warehouse.upsert(SPEC, [ligne(1, "radiant"), ligne(1, "dire")])

    assert result.received == 2
    assert result.duplicates_in_batch == 1
    assert warehouse.count(SPEC.qualified_name) == 1
    assert warehouse.scalar("SELECT vainqueur FROM raw.matchs") == "dire"


def test_une_ligne_revue_est_rafraichie_sans_perdre_sa_date_d_arrivee(
    warehouse: Warehouse,
) -> None:
    warehouse.ensure_table(SPEC)
    warehouse.upsert(SPEC, [ligne(1, "radiant", PREMIERE_ECRITURE)])

    warehouse.upsert(SPEC, [ligne(1, "dire", SECONDE_ECRITURE)])

    vainqueur, arrivee = warehouse.query("SELECT vainqueur, ingested_at FROM raw.matchs")[0]
    assert vainqueur == "dire"
    assert arrivee == PREMIERE_ECRITURE


def test_un_lot_vide_ne_touche_pas_l_entrepot(warehouse: Warehouse) -> None:
    warehouse.ensure_table(SPEC)

    result = warehouse.upsert(SPEC, [])

    assert result.received == 0
    assert warehouse.count(SPEC.qualified_name) == 0


def test_une_transaction_interrompue_ne_laisse_rien_derriere(warehouse: Warehouse) -> None:
    """Données et état s'écrivent ensemble : c'est ce qui rend la reprise sûre."""
    warehouse.ensure_table(SPEC)

    with pytest.raises(RuntimeError, match="coupure"), warehouse.transaction():
        warehouse.upsert(SPEC, [ligne(1, "radiant")])
        warehouse.save_state(IngestionState(key=KEY, high_watermark="1"))
        raise RuntimeError("coupure")

    assert warehouse.count(SPEC.qualified_name) == 0
    assert warehouse.load_state(KEY) is None


# -- État -------------------------------------------------------------------


def test_un_flux_jamais_collecte_n_a_pas_d_etat(warehouse: Warehouse) -> None:
    assert warehouse.load_state(KEY) is None


def test_l_etat_se_relit_tel_qu_il_a_ete_ecrit(warehouse: Warehouse) -> None:
    state = IngestionState(
        key=KEY,
        high_watermark="900",
        backfill_cursor="100",
        records_seen=42,
        last_run_at=PREMIERE_ECRITURE,
    )

    warehouse.save_state(state)

    assert warehouse.load_state(KEY) == state


def test_enregistrer_l_etat_deux_fois_ecrase_au_lieu_d_empiler(warehouse: Warehouse) -> None:
    warehouse.save_state(IngestionState(key=KEY, high_watermark="900", backfill_cursor="800"))
    warehouse.save_state(IngestionState(key=KEY, high_watermark="950", backfill_cursor="800"))

    assert warehouse.scalar(f"SELECT count(*) FROM {STATE_TABLE}") == 1
    state = warehouse.load_state(KEY)
    assert state is not None
    assert state.high_watermark == "950"


def test_une_execution_laisse_une_trace(warehouse: Warehouse) -> None:
    before = IngestionState.empty(KEY)
    after = IngestionState(key=KEY, high_watermark="900", backfill_cursor="800")
    report = RunReport(
        key=KEY,
        mode=Mode.CATCH_UP,
        started_at=PREMIERE_ECRITURE,
        finished_at=SECONDE_ECRITURE,
        pages=2,
        records=12,
        inserted=12,
        updated=0,
        stop_reason=StopReason.EXHAUSTED,
        state_before=before,
        state_after=after,
    )

    run_id = warehouse.record_run(report)

    trace = warehouse.query(
        f"SELECT source, mode, pages, records, stop_reason, backfill_cursor "
        f"FROM {RUNS_TABLE} WHERE run_id = ?",
        [run_id],
    )
    assert trace == [("fournisseur", "catchup", 2, 12, "exhausted", "800")]
