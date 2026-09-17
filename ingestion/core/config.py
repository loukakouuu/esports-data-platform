"""Configuration.

Tout ce qui dépend de l'environnement se lit ici, et nulle part ailleurs : aucune
clé, aucun contact ne doit apparaître dans le reste du code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
"""Racine du dépôt : les chemins par défaut en découlent."""

DEFAULT_WAREHOUSE = PROJECT_ROOT / "warehouse" / "esports.duckdb"

DEFAULT_USER_AGENT = (
    "esports-data-platform/0.1 (https://github.com/loukakouuu/esports-data-platform)"
)
"""User-Agent descriptif, sans donnée personnelle.

Liquipedia exige en plus un contact : celui-là vit dans `LIQUIPEDIA_USER_AGENT`,
jamais en dur dans le dépôt.
"""


def _optional(name: str) -> str | None:
    """Lit une variable d'environnement en traitant la chaîne vide comme absente.

    `.env.example` documente les clés avec une valeur vide : sans cela, une clé
    non renseignée serait transmise telle quelle aux API.
    """
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class Settings:
    """Paramètres effectifs d'une exécution."""

    warehouse_path: Path
    user_agent: str
    opendota_api_key: str | None = None
    balldontlie_api_key: str | None = None
    liquipedia_user_agent: str | None = None

    @classmethod
    def from_env(
        cls, *, warehouse_path: Path | None = None, use_dotenv: bool = True
    ) -> Settings:
        """Construit les paramètres depuis `.env` puis l'environnement du processus.

        L'environnement du processus l'emporte sur `.env` — c'est ce que fait la CI.
        """
        if use_dotenv:
            load_dotenv(PROJECT_ROOT / ".env", override=False)
        configured = warehouse_path or _optional("WAREHOUSE_PATH")
        return cls(
            warehouse_path=Path(configured) if configured else DEFAULT_WAREHOUSE,
            user_agent=_optional("USER_AGENT") or DEFAULT_USER_AGENT,
            opendota_api_key=_optional("OPENDOTA_API_KEY"),
            balldontlie_api_key=_optional("BALLDONTLIE_API_KEY"),
            liquipedia_user_agent=_optional("LIQUIPEDIA_USER_AGENT"),
        )
