"""Registre des sources.

Une source disponible est une entrée de plus ici, et rien d'autre à changer :
c'est le seul endroit où le nom d'un fournisseur rencontre le reste du code.
"""

from __future__ import annotations

from collections.abc import Callable

from ingestion.core.config import Settings
from ingestion.core.source import Source
from ingestion.sources import opendota

_FACTORIES: dict[str, Callable[[Settings], Source]] = {
    str(opendota.KEY): lambda settings: opendota.OpenDotaProMatches(settings=settings),
}


def available() -> tuple[str, ...]:
    """Noms de flux utilisables, tels que la ligne de commande les attend."""
    return tuple(sorted(_FACTORIES))


def build(name: str, settings: Settings) -> Source:
    """Instancie un flux par son nom."""
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(available())
        raise KeyError(f"source inconnue « {name} » — disponibles : {known}") from None
    return factory(settings)
