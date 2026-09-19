"""Journalisation : une ingestion qui ne dit pas ce qu'elle fait ne se débogue pas."""

from __future__ import annotations

import io
import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-24s %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def force_utf8_output() -> None:
    """Passe les sorties en UTF-8 quand la console n'y est pas.

    La console Windows répond `cp1252`, qui ne sait écrire ni « é » ni « — ».
    Elle ne lève pourtant aucune erreur : elle remplace silencieusement, et un
    journal en français devient illisible. Autant l'annoncer à Python.
    """
    for flux in (sys.stdout, sys.stderr):
        if isinstance(flux, io.TextIOWrapper) and (flux.encoding or "").lower() != "utf-8":
            flux.reconfigure(encoding="utf-8", errors="backslashreplace")


def setup_logging(*, verbose: bool = False) -> None:
    """Installe une sortie console lisible. Idempotent : un seul gestionnaire."""
    force_utf8_output()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
