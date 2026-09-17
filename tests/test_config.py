"""La configuration décide de ce qui part sur le réseau : elle mérite des tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.core.config import DEFAULT_USER_AGENT, DEFAULT_WAREHOUSE, Settings

ENV_VARS = (
    "WAREHOUSE_PATH",
    "USER_AGENT",
    "OPENDOTA_API_KEY",
    "BALLDONTLIE_API_KEY",
    "LIQUIPEDIA_USER_AGENT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isole les tests de l'environnement réel de la machine."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_valeurs_par_defaut() -> None:
    settings = Settings.from_env(use_dotenv=False)

    assert settings.warehouse_path == DEFAULT_WAREHOUSE
    assert settings.user_agent == DEFAULT_USER_AGENT
    assert settings.opendota_api_key is None


def test_une_cle_vide_vaut_une_cle_absente(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` documente les clés à vide : ne pas les transmettre aux API."""
    monkeypatch.setenv("OPENDOTA_API_KEY", "   ")

    assert Settings.from_env(use_dotenv=False).opendota_api_key is None


def test_l_environnement_l_emporte(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENDOTA_API_KEY", "secret")
    monkeypatch.setenv("WAREHOUSE_PATH", str(tmp_path / "test.duckdb"))

    settings = Settings.from_env(use_dotenv=False)

    assert settings.opendota_api_key == "secret"
    assert settings.warehouse_path == tmp_path / "test.duckdb"


def test_le_user_agent_par_defaut_est_identifiant_et_impersonnel() -> None:
    """Un agent générique se fait bloquer ; un contact en dur fuiterait une donnée."""
    assert "esports-data-platform" in DEFAULT_USER_AGENT
    assert "github.com/loukakouuu" in DEFAULT_USER_AGENT
    assert "@" not in DEFAULT_USER_AGENT
