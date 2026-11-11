"""Lecture d'une page de match Liquipedia, telle que le wiki la rend.

Les scores d'un match Valorant ne sont pas dans son wikitexte : la page ne porte
qu'un identifiant de partie, et c'est Liquipedia qui interroge l'API de Riot au
moment du rendu. Tout ce qui fait l'intérêt d'un match — score par carte,
statistiques par joueur, déroulé manche par manche — n'existe donc que dans le
HTML rendu.

Ce module en tire un document structuré. Il ne dépend que de la forme du HTML,
pas du réseau : il se teste sur une page capturée, et peut rejouer une page
déjà stockée le jour où il faudra en extraire davantage.
"""

from __future__ import annotations

import re
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

ROOT = ".match-bm"

_HEAVY_ATTRIBUTES = re.compile(r'\s(?:src|srcset|style|decoding|loading|width|height)="[^"]*"')
_INTEGER = re.compile(r"\d+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

_SIDES = ("atk", "def")
NON_BREAKING_HYPHEN = "\u2011"

# Le libellé affiché, tel que Liquipedia l'écrit, vers le nom du champ.
_PLAYER_STATS = {
    "acs": "acs",
    "kast": "kast",
    "adr": "adr",
    "hs%": "headshot_pct",
}
_TEAM_STATS = {
    "first kills": "first_kills",
    "thrifties": "thrifties",
    "flawless": "flawless",
    "post plant": "post_plant",
    "clutches": "clutches",
}


def compact_html(html: str) -> str | None:
    """Réduit une page à ce qu'il faut pour la relire, ou `None` sans match dedans.

    Une page pèse 370 ko, dont l'essentiel en images et en historique des
    confrontations. Le sous-arbre du match, allégé, tient en 13 ko une fois
    compressé — assez peu pour le garder, et s'épargner de redemander la page à
    raison d'une toutes les trente secondes.
    """
    root = LexborHTMLParser(html).css_first(ROOT)
    if root is None:
        return None
    for extra in root.css(".match-bm-match-additional"):
        extra.decompose()
    return _HEAVY_ATTRIBUTES.sub("", root.html or "")


def parse_match_page(html: str) -> dict[str, Any]:
    """Tire d'une page rendue le document d'un match.

    Une page sans bloc de match — ancien format, page vidée — rend un document
    au statut `unparsed` plutôt qu'une erreur : la page existe, elle ne dit
    simplement rien d'exploitable.
    """
    root = LexborHTMLParser(html).css_first(ROOT)
    if root is None:
        return {"status": "unparsed", "teams": [], "veto": [], "maps": []}

    timer = root.css_first(".match-bm-match-header-date [data-timestamp]")
    tournament = root.css_first(".match-bm-match-header-tournament a")
    teams = [_team(node) for node in root.css(".match-bm-match-header-team")]
    score = _integers(_text(root.css_first(".match-bm-match-header-result")))

    return {
        "status": _status(root, timer),
        "played_at_epoch": _int(timer.attributes.get("data-timestamp")) if timer else None,
        "tournament_page": _page(tournament),
        "tournament_name": _text(tournament),
        "teams": teams,
        "score": score[:2] if len(score) >= 2 else None,
        "veto": [_veto(card, order) for order, card in enumerate(_veto_cards(root), start=1)],
        "maps": [_map(game, number) for number, game in enumerate(_games(root), start=1)],
    }


# -- En-tête ---------------------------------------------------------------


def _status(root: LexborNode, timer: LexborNode | None) -> str:
    label = _text(root.css_first(".match-bm-match-header-result-text"))
    if label:
        return label.lower()
    if timer is not None and timer.attributes.get("data-finished") == "finished":
        return "finished"
    return "unknown"


def _team(node: LexborNode) -> dict[str, Any]:
    long_name = node.css_first(".match-bm-match-header-team-long")
    link = long_name.css_first("a") if long_name else node.css_first("a")
    return {
        "page": _page(link),
        "name": _text(long_name) or _title(link),
        "short": _text(node.css_first(".match-bm-match-header-team-short")),
    }


def _veto_cards(root: LexborNode) -> list[LexborNode]:
    return root.css(".match-bm-map-veto-card")


def _veto(card: LexborNode, order: int) -> dict[str, Any]:
    classes = (card.attributes.get("class") or "").split()
    prefix = "match-bm-map-veto-card--"
    action = next((name[len(prefix) :] for name in classes if name.startswith(prefix)), None)
    team = card.css_first(".match-bm-map-veto-card-map-info a")
    return {
        "order": order,
        "action": action,
        "map": _text(card.css_first(".match-bm-map-veto-card-map-name")),
        # Le décideur n'est choisi par personne : il reste sans équipe.
        "team": _title(team),
    }


# -- Cartes ----------------------------------------------------------------


def _games(root: LexborNode) -> list[LexborNode]:
    """Les blocs de carte. Le premier onglet, « Overall », n'en est pas un."""
    zones = [zone for zone in root.css("[data-toggle-area-content]") if _has_summary(zone)]
    if zones:
        return zones
    # Un match en une seule carte n'a pas d'onglets : le bloc est à la racine.
    return [root] if _has_summary(root) else []


def _has_summary(node: LexborNode) -> bool:
    return node.css_first(".match-bm-lol-game-summary") is not None


def _map(game: LexborNode, number: int) -> dict[str, Any]:
    summary = game.css_first(".match-bm-lol-game-summary") or game
    score = _integers(_text(summary.css_first(".match-bm-lol-game-summary-score")))
    return {
        "number": number,
        "map": _text(summary.css_first(".match-bm-lol-game-summary-map")),
        "score": score[:2] if len(score) >= 2 else None,
        "length": _text(summary.css_first(".match-bm-lol-game-summary-length")),
        "halves": [
            _halves(team) for team in summary.css(".match-bm-lol-game-summary-team")[:2]
        ],
        "team_stats": _team_stats(game),
        "players": _players(game),
        "rounds": _rounds(game),
    }


def _halves(team: LexborNode) -> list[dict[str, Any]]:
    """Les manches gagnées par mi-temps, avec le camp tenu. Prolongations comprises."""
    halves = []
    for half in team.css(".match-bm-game-summary-team-halves-half"):
        halves.append({"side": _modifier(half), "rounds": _int(_text(half))})
    return halves


def _team_stats(game: LexborNode) -> dict[str, list[str | None]]:
    stats: dict[str, list[str | None]] = {}
    for row in game.css(".match-bm-team-stats-list-row"):
        cells = row.css(".match-bm-team-stats-list-cell")
        if len(cells) != 3:
            continue
        label = (_text(cells[1]) or "").lower()
        key = _TEAM_STATS.get(label)
        if key:
            stats[key] = [_text(cells[0]), _text(cells[2])]
    return stats


def _players(game: LexborNode) -> list[dict[str, Any]]:
    players = []
    for side, team in enumerate(game.css(".match-bm-players-team")[:2], start=1):
        for node in team.css(".match-bm-players-player"):
            players.append(_player(node, side))
    return players


def _player(node: LexborNode, side: int) -> dict[str, Any]:
    name = node.css_first(".match-bm-players-player-name a")
    agent = node.css_first(".match-bm-players-player-icon a")
    player: dict[str, Any] = {
        "side": side,
        "page": _page(name),
        "name": _text(name),
        "agent": _title(agent),
    }
    for stat in node.css(".match-bm-players-player-stat"):
        label = (_text(stat.css_first(".match-bm-players-player-stat-title")) or "").lower()
        value = _text(stat.css_first(".match-bm-players-player-stat-data"))
        numbers = _numbers(value)
        if label == "kda" and len(numbers) == 3:
            player["kills"], player["deaths"], player["assists"] = (int(n) for n in numbers)
        elif label.replace(" ", "") == "fk/fd" and len(numbers) == 2:
            player["first_kills"], player["first_deaths"] = (int(n) for n in numbers)
        elif label in _PLAYER_STATS and numbers:
            player[_PLAYER_STATS[label]] = numbers[0]
    return player


def _rounds(game: LexborNode) -> list[dict[str, Any]]:
    """Le déroulé manche par manche.

    Deux vues se complètent : le bandeau dit qui gagne et de quel camp, le
    détail dit comment, et qui a ouvert ou conclu la manche.
    """
    details = {
        _int(_text(detail.css_first(".match-bm-match-round-detail-header"))): detail
        for detail in game.css(".match-bm-match-round-detail")
    }
    rounds = []
    for overview in game.css(".match-bm-rounds-overview-round"):
        number = _int(_text(overview.css_first(".match-bm-rounds-overview-round-title")))
        winner, side = _round_winner(overview)
        if number is None or winner is None:
            continue  # manche annoncée mais pas encore jouée
        entry: dict[str, Any] = {"number": number, "winner": winner, "winner_side": side}
        detail = details.get(number)
        if detail is not None:
            entry["outcome"] = _text(
                detail.css_first(".match-bm-match-round-detail-body-result-desc")
            )
            entry.update(_round_events(detail))
        rounds.append(entry)
    return rounds


def _round_winner(overview: LexborNode) -> tuple[int | None, str | None]:
    """Le vainqueur est la case qui porte l'icône : la première pour le camp 1."""
    for side, cell in enumerate(overview.css(".match-bm-rounds-overview-round-outcome")[:2], 1):
        icon = cell.css_first(".match-bm-rounds-overview-round-outcome-icon")
        if icon is not None:
            return side, _modifier(icon)
    return None, None


def _round_events(detail: LexborNode) -> dict[str, str | None]:
    events: dict[str, str | None] = {}
    body = detail.css_first(".match-bm-match-round-detail-body")
    if body is None:
        return events
    for span in body.css("span"):
        label = (_text(span.css_first("b")) or "").lower()
        player = span.css_first("a[title]")
        if player is None:
            continue
        if label == "first kill":
            events["first_kill"] = _page(player)
        elif label == "clutch":
            events["clutch"] = _page(player)
    return events


# -- Petites lectures ------------------------------------------------------


def _text(node: LexborNode | None) -> str | None:
    if node is None:
        return None
    # Liquipedia sème des espaces et des tirets insécables dans ses scores.
    text = node.text(deep=True).replace("\xa0", " ").replace(NON_BREAKING_HYPHEN, "-")
    text = " ".join(text.split())
    return text or None


def _title(node: LexborNode | None) -> str | None:
    return (node.attributes.get("title") or None) if node is not None else None


def _page(node: LexborNode | None) -> str | None:
    """Le titre de page visé par un lien : la seule identité stable d'un joueur."""
    return _title(node)


def _modifier(node: LexborNode) -> str | None:
    """Le camp, porté par un suffixe de classe : `…--atk` ou `…--def`."""
    for name in (node.attributes.get("class") or "").split():
        suffix = name.rsplit("--", 1)[-1]
        if "--" in name and suffix in _SIDES:
            return suffix
    return None


def _int(text: str | None) -> int | None:
    match = _INTEGER.search(text or "")
    return int(match.group()) if match else None


def _integers(text: str | None) -> list[int]:
    return [int(value) for value in _INTEGER.findall(text or "")]


def _numbers(text: str | None) -> list[float]:
    return [float(value) for value in _NUMBER.findall(text or "")]
