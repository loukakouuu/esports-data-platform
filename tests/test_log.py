"""La sortie console, sur une machine dont la console n'est pas en UTF-8."""

from __future__ import annotations

import io
import sys

import pytest

from ingestion.core.log import force_utf8_output


def console_cp1252() -> io.TextIOWrapper:
    """Reproduit une console Windows : cp1252, et qui ne se plaint jamais."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="surrogateescape")


def test_une_console_cp1252_passe_en_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    flux = console_cp1252()
    monkeypatch.setattr(sys, "stdout", flux)

    force_utf8_output()

    assert flux.encoding.lower() == "utf-8"


def test_le_francais_traverse_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans cela, « frontière » s'écrit « fronti?re » sans que rien ne le signale."""
    flux = console_cp1252()
    monkeypatch.setattr(sys, "stdout", flux)
    force_utf8_output()

    print("frontière — sommet « inconnu »", file=flux)
    flux.flush()

    ecrit = flux.buffer.getvalue().decode("utf-8")  # type: ignore[attr-defined]
    assert "frontière — sommet « inconnu »" in ecrit


def test_une_console_deja_en_utf8_est_laissee_tranquille(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flux = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", flux)

    force_utf8_output()

    assert flux.encoding.lower() == "utf-8"


def test_une_sortie_qui_n_est_pas_un_flux_texte_ne_casse_rien(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pytest remplace stdout par un objet qui n'a pas `reconfigure`."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    force_utf8_output()
