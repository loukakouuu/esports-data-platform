"""Tests de données : les attentes elles-mêmes, puis leur verdict sur du vrai brut."""

from __future__ import annotations

from datetime import UTC, datetime

from ingestion.core import quality, runner
from ingestion.core.quality import Check, Severity
from ingestion.core.warehouse import Warehouse
from ingestion.sources.opendota import CHECKS, TABLE, OpenDotaProMatches

TABLE_NAME = TABLE.qualified_name


def resultats_par_nom(results: list[quality.CheckResult]) -> dict[str, quality.CheckResult]:
    return {result.check.name: result for result in results}


# -- Le cadre ---------------------------------------------------------------


def test_une_attente_sur_une_table_absente_est_ignoree_et_non_echouee(
    warehouse: Warehouse,
) -> None:
    """Un flux jamais collecté n'est pas un flux en faute."""
    check = Check.rows_where(
        "jamais_collecte",
        description="table absente",
        table="raw.inexistante",
        violation="1 = 1",
    )

    (result,) = quality.run_checks(warehouse, [check])

    assert result.skipped
    assert not result.failed
    assert not quality.has_blocking_failure([result])


def test_une_attente_satisfaite_passe(warehouse: Warehouse) -> None:
    warehouse.ensure_table(TABLE)
    check = Check.rows_where(
        "toujours_vrai",
        description="aucune ligne fautive",
        table=TABLE_NAME,
        violation="match_id < 0",
    )

    (result,) = quality.run_checks(warehouse, [check])

    assert result.passed
    assert result.violations == 0


def test_un_avertissement_en_defaut_ne_bloque_pas(warehouse: Warehouse) -> None:
    """Une donnée que la source livre incomplète n'invalide pas la collecte."""
    warehouse.ensure_table(TABLE)
    warehouse.upsert(TABLE, [ligne_minimale(1)])
    check = Check.rows_where(
        "toujours_faux",
        description="toutes les lignes sont fautives",
        table=TABLE_NAME,
        violation="1 = 1",
        severity=Severity.WARN,
    )

    (result,) = quality.run_checks(warehouse, [check])

    assert result.failed
    assert not result.is_blocking
    assert not quality.has_blocking_failure([result])


def test_une_erreur_en_defaut_bloque(warehouse: Warehouse) -> None:
    warehouse.ensure_table(TABLE)
    warehouse.upsert(TABLE, [ligne_minimale(1)])
    check = Check.rows_where(
        "toujours_faux",
        description="toutes les lignes sont fautives",
        table=TABLE_NAME,
        violation="1 = 1",
    )

    (result,) = quality.run_checks(warehouse, [check])

    assert result.is_blocking
    assert quality.has_blocking_failure([result])


def test_l_attente_d_unicite_compte_les_valeurs_repetees(warehouse: Warehouse) -> None:
    check = Check.unique(
        "identifiants_uniques",
        description="valeur répétée",
        table="raw.essai",
        column="id",
    )
    warehouse.query("CREATE SCHEMA IF NOT EXISTS raw")
    warehouse.query("CREATE TABLE raw.essai (id BIGINT)")
    warehouse.query("INSERT INTO raw.essai VALUES (1), (1), (2), (3), (3)")

    (result,) = quality.run_checks(warehouse, [check])

    assert result.violations == 2


# -- Les attentes d'OpenDota, sur du brut réellement ingéré ------------------


def test_le_brut_collecte_satisfait_toutes_les_attentes_bloquantes(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    """Le jeu d'essai vient de l'API : ce test dit que la normalisation tient."""
    runner.run(opendota, warehouse, max_pages=10)

    results = quality.run_checks(warehouse, opendota.checks)

    assert not quality.has_blocking_failure(results)
    assert len(results) == len(CHECKS)


def test_les_equipes_non_identifiees_sont_signalees_sans_bloquer(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    """Deux matchs du jeu d'essai n'ont pas d'équipe identifiée : c'est la source, pas nous."""
    runner.run(opendota, warehouse, max_pages=10)

    resultat = resultats_par_nom(quality.run_checks(warehouse, opendota.checks))

    equipes = resultat["equipes_identifiees"]
    assert equipes.failed
    assert not equipes.is_blocking
    assert equipes.violations == 2


def test_une_duree_aberrante_est_attrapee(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    runner.run(opendota, warehouse, max_pages=10)
    warehouse.query(f"UPDATE {TABLE_NAME} SET duration_seconds = 100000 WHERE true")

    resultat = resultats_par_nom(quality.run_checks(warehouse, opendota.checks))

    assert resultat["duree_plausible"].is_blocking


def test_un_match_sans_vainqueur_est_attrape(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    runner.run(opendota, warehouse, max_pages=10)
    warehouse.query(f"UPDATE {TABLE_NAME} SET radiant_win = NULL WHERE true")

    resultat = resultats_par_nom(quality.run_checks(warehouse, opendota.checks))

    assert resultat["issue_connue"].is_blocking


def test_une_date_anterieure_a_dota2_est_attrapee(
    opendota: OpenDotaProMatches, warehouse: Warehouse
) -> None:
    """Le piège classique : une heure epoch lue en millisecondes, ou l'inverse."""
    runner.run(opendota, warehouse, max_pages=10)
    warehouse.query(
        f"UPDATE {TABLE_NAME} SET start_time = TIMESTAMPTZ '1970-01-01 00:00:00+00' WHERE true"
    )

    resultat = resultats_par_nom(quality.run_checks(warehouse, opendota.checks))

    assert resultat["debut_plausible"].is_blocking


def ligne_minimale(match_id: int) -> dict[str, object]:
    """Une ligne conforme à la table, sans prétendre décrire un vrai match."""
    now = datetime.now(UTC)
    row: dict[str, object] = dict.fromkeys(TABLE.column_names)
    row.update(
        {
            "match_id": match_id,
            "payload": "{}",
            "ingested_at": now,
            "refreshed_at": now,
        }
    )
    return row
