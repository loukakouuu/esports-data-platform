"""Registre des sources.

Une source disponible est une entrée de plus ici, et rien d'autre à changer :
c'est le seul endroit où le nom d'un fournisseur rencontre le reste du code.
"""

from __future__ import annotations

from collections.abc import Callable

from ingestion.core.config import Settings
from ingestion.core.source import Source
from ingestion.sources import liquipedia, liquipedia_matches, opendota

Factory = Callable[[Settings], Source]


def _tournois(wiki: liquipedia.Wiki) -> Factory:
    """Un wiki par discipline, mais une seule implémentation derrière."""

    def construire(settings: Settings) -> Source:
        return liquipedia.LiquipediaTournaments(wiki, settings=settings)

    return construire


def _liens_vers_les_matchs(wiki: liquipedia.Wiki) -> Factory:
    def construire(settings: Settings) -> Source:
        return liquipedia_matches.LiquipediaTournamentMatches(wiki, settings=settings)

    return construire


def _pages_de_match(wiki: liquipedia.Wiki) -> Factory:
    def construire(settings: Settings) -> Source:
        return liquipedia_matches.LiquipediaMatchPages(wiki, settings=settings)

    return construire


# Les pages de match ne sont lues que pour Valorant : leur forme rendue est
# propre à chaque wiki, et seule celle-ci a été éprouvée sur des pages réelles.
WIKIS_AVEC_MATCHS = (liquipedia.VALORANT,)

_FACTORIES: dict[str, Factory] = {
    str(opendota.KEY): lambda settings: opendota.OpenDotaProMatches(settings=settings),
    **{str(wiki.key): _tournois(wiki) for wiki in liquipedia.WIKIS},
    **{
        str(wiki.flux("tournament_matches")): _liens_vers_les_matchs(wiki)
        for wiki in WIKIS_AVEC_MATCHS
    },
    **{str(wiki.flux("match_pages")): _pages_de_match(wiki) for wiki in WIKIS_AVEC_MATCHS},
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
