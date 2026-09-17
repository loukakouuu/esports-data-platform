"""Ligne de commande de l'ingestion.

esports-ingest sources
esports-ingest run opendota.pro_matches --pages 5
esports-ingest run opendota.pro_matches --mode backfill --pages 20
esports-ingest state
esports-ingest check
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from ingestion import __version__, sources
from ingestion.core import quality, runner
from ingestion.core.config import Settings
from ingestion.core.http import HttpError
from ingestion.core.log import setup_logging
from ingestion.core.source import SourceError
from ingestion.core.state import Mode
from ingestion.core.warehouse import Warehouse

logger = logging.getLogger("ingestion")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="esports-ingest",
        description="Collecte incrémentale et idempotente des sources esport.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="journal détaillé")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        metavar="CHEMIN",
        help="entrepôt DuckDB à utiliser (défaut : warehouse/esports.duckdb)",
    )

    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("sources", help="liste les flux disponibles")

    run_command = commands.add_parser("run", help="exécute une ingestion")
    run_command.add_argument("source", choices=sources.available(), help="flux à collecter")
    run_command.add_argument(
        "--mode",
        type=Mode,
        choices=tuple(Mode),
        default=Mode.CATCH_UP,
        help="catchup : repartir du sommet ; backfill : creuser l'historique",
    )
    run_command.add_argument(
        "--pages",
        type=int,
        default=runner.DEFAULT_MAX_PAGES,
        metavar="N",
        help=f"plafond de pages (défaut : {runner.DEFAULT_MAX_PAGES})",
    )

    commands.add_parser("state", help="affiche l'avancement de chaque flux")

    check_command = commands.add_parser("check", help="vérifie les données collectées")
    check_command.add_argument(
        "source",
        nargs="?",
        choices=sources.available(),
        help="flux à vérifier (défaut : tous)",
    )

    return parser


def _command_sources() -> int:
    for name in sources.available():
        print(name)
    return 0


def _command_run(settings: Settings, name: str, mode: Mode, pages: int) -> int:
    with (
        Warehouse.open(settings.warehouse_path) as warehouse,
        sources.build(name, settings) as source,
    ):
        report = runner.run(source, warehouse, mode=mode, max_pages=pages)
    print(report.summary())
    return 0


def _command_state(settings: Settings) -> int:
    with Warehouse.open(settings.warehouse_path) as warehouse:
        states = warehouse.all_states()
    if not states:
        print("Aucune ingestion enregistrée.")
        return 0
    for state in states:
        last_run = state.last_run_at.isoformat(timespec="seconds") if state.last_run_at else "—"
        print(
            f"{state.key} : sommet {state.high_watermark or '—'}, "
            f"frontière {state.backfill_cursor or '—'}, "
            f"{state.records_seen} enregistrement(s) vus, dernière exécution {last_run}"
        )
    return 0


def _command_check(settings: Settings, name: str | None) -> int:
    """Rend 1 si une attente bloquante est en défaut : la CI peut s'y fier."""
    noms = (name,) if name else sources.available()
    results = []
    with Warehouse.open(settings.warehouse_path) as warehouse:
        for nom in noms:
            source = sources.build(nom, settings)
            try:
                print(f"{source.key} ({source.table.qualified_name})")
                for result in quality.run_checks(warehouse, source.checks):
                    results.append(result)
                    print(f"  {result.summary()}")
            finally:
                source.close()

    en_defaut = sum(1 for result in results if result.failed)
    bloquantes = sum(1 for result in results if result.is_blocking)
    print(
        f"\n{len(results)} attente(s), {en_defaut} en défaut, dont {bloquantes} bloquante(s)."
    )
    return 1 if quality.has_blocking_failure(results) else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)
    settings = Settings.from_env(warehouse_path=args.db)

    try:
        if args.command == "sources":
            return _command_sources()
        if args.command == "run":
            return _command_run(settings, args.source, args.mode, args.pages)
        if args.command == "state":
            return _command_state(settings)
        if args.command == "check":
            return _command_check(settings, args.source)
    except (SourceError, HttpError, KeyError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("interrompu — l'avancement déjà écrit est conservé")
        return 130

    parser.error(f"commande inconnue : {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
