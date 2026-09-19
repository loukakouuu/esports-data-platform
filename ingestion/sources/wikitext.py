"""Lecture de modèles MediaWiki.

Liquipedia ne rend pas de JSON : ses pages sont du wikitexte, et ce qu'on veut
tient dans un modèle `{{Infobox league|...}}`. Il n'existe pas d'API pour ne
demander que ce modèle — il faut donc le découper soi-même.

Ce module ne prétend pas interpréter le wikitexte. Il fait deux choses, et les
fait honnêtement : retrouver un modèle en respectant l'imbrication des
accolades, et découper ses champs sur les `|` de premier niveau. Les valeurs
sont rendues telles quelles ; `strip_markup` propose un nettoyage séparé, que
l'appelant applique où il le juge sûr.
"""

from __future__ import annotations

import re

_EXTERNAL_LINK = re.compile(r"\[(?:https?:)?//[^\s\]]+\s+([^\]]*)\]")
"""`[https://exemple.fr Libellé]` — on garde le libellé."""

_BARE_EXTERNAL_LINK = re.compile(r"\[(?:https?:)?//[^\s\]]+\]")
_INTERNAL_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]")
"""`[[Cible|Libellé]]` ou `[[Cible]]` — on garde ce qui s'affiche."""

_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_HTML_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_BOLD_ITALIC = re.compile(r"'{2,5}")
_WHITESPACE = re.compile(r"\s+")

_OPENERS = {"{{": "}}", "[[": "]]"}

_COMMENT_OPEN = "<!--"


def strip_comments(wikitext: str) -> str:
    """Retire les commentaires HTML, y compris celui qu'on a oublié de fermer.

    Ce n'est pas de la cosmétique : un commentaire qui contient une barre
    verticale ou une accolade tromperait le découpage des champs. Sur les 25 000
    tournois collectés, 340 pages en portaient un — assez pour vider une date ou
    coller « <!-- » à la fin d'un tier.

    Un commentaire jamais fermé court jusqu'à la fin du texte, comme MediaWiki
    lui-même le rend.
    """
    cleaned = _HTML_COMMENT.sub("", wikitext)
    unterminated = cleaned.find(_COMMENT_OPEN)
    return cleaned if unterminated < 0 else cleaned[:unterminated]


def find_template(wikitext: str, name: str) -> str | None:
    """Rend le corps du premier modèle `{{name ...}}`, ou `None` s'il n'y en a pas.

    Le découpage suit les accolades imbriquées : un modèle dans un modèle ne
    referme pas celui qui l'englobe.
    """
    wikitext = strip_comments(wikitext)
    marker = "{{" + name.lower()
    start = wikitext.lower().find(marker)
    if start < 0:
        return None

    depth = 0
    index = start
    while index < len(wikitext):
        if wikitext.startswith("{{", index):
            depth += 1
            index += 2
        elif wikitext.startswith("}}", index):
            depth -= 1
            index += 2
            if depth == 0:
                return wikitext[start + 2 : index - 2]
        else:
            index += 1
    return None  # modèle jamais refermé : page en cours d'édition, on n'invente rien


def parse_fields(body: str) -> dict[str, str]:
    """Découpe un corps de modèle en champs `clé=valeur`.

    Seuls les `|` de premier niveau séparent : ceux qui vivent dans un modèle ou
    un lien internes appartiennent à la valeur. Les clés sont mises en minuscules
    — Liquipedia alterne `sdate` et `sDate` selon les pages.

    Les commentaires disparaissent avant le découpage : une barre verticale
    commentée n'est pas un séparateur.
    """
    fields: dict[str, str] = {}
    for chunk in _split_top_level(strip_comments(body))[1:]:
        key, separator, value = chunk.partition("=")
        if not separator:
            continue  # paramètre positionnel : sans nom, on ne saurait qu'en faire
        key = key.strip().lower()
        if key:
            fields[key] = value.strip()
    return fields


def _split_top_level(body: str) -> list[str]:
    """Découpe sur les `|` non imbriqués. Le premier morceau est le nom du modèle."""
    chunks: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(body):
        pair = body[index : index + 2]
        if pair in _OPENERS:
            depth += 1
            current.append(pair)
            index += 2
            continue
        if pair in _OPENERS.values():
            depth = max(depth - 1, 0)
            current.append(pair)
            index += 2
            continue
        character = body[index]
        if character == "|" and depth == 0:
            chunks.append("".join(current))
            current = []
        else:
            current.append(character)
        index += 1
    chunks.append("".join(current))
    return chunks


def strip_markup(value: str) -> str:
    """Rend une valeur lisible : liens, modèles et balises retirés.

    À n'appliquer que là où le balisage n'est que décoration — un nom de ville,
    un organisateur. La valeur brute reste conservée par l'appelant, si bien
    qu'un nettoyage trop zélé ne perd rien.
    """
    cleaned = _HTML_COMMENT.sub("", value)
    cleaned = _HTML_BREAK.sub(" ", cleaned)
    cleaned = _EXTERNAL_LINK.sub(r"\1", cleaned)
    cleaned = _BARE_EXTERNAL_LINK.sub("", cleaned)
    cleaned = _INTERNAL_LINK.sub(r"\1", cleaned)
    # Les modèles s'imbriquent : on retire du plus interne au plus externe.
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _TEMPLATE.sub("", cleaned)
    cleaned = _HTML_TAG.sub("", cleaned)
    cleaned = _BOLD_ITALIC.sub("", cleaned)
    return _WHITESPACE.sub(" ", cleaned).strip()
