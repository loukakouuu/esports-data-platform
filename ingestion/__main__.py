"""Permet `python -m ingestion`, sans passer par le script installé."""

from ingestion.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
