"""Journalisation : une ingestion qui ne dit pas ce qu'elle fait ne se débogue pas."""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-24s %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def setup_logging(*, verbose: bool = False) -> None:
    """Installe une sortie console lisible. Idempotent : un seul gestionnaire."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
