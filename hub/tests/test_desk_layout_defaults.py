"""Defaults layout desk : panneaux compacts, canvas QGIS prioritaire."""

from __future__ import annotations

from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8"
)


def test_panneaux_compacts_par_defaut_css() -> None:
    assert "var(--chat-w,300px)" in _DESK
    assert "var(--publi-w,240px)" in _DESK
    assert "var(--chat-w,260px)" in _DESK  # narrow <1100px


def test_update_layout_ne_gonfle_plus_le_chat() -> None:
    assert "tientEnPleineHauteur" not in _DESK
    assert "qgisWAtFullH" not in _DESK
    assert "const CHAT_NOMINAL = 300" in _DESK
    assert "const CHAT_NOMINAL_NARROW = 260" in _DESK
    assert "function _defaultChatWidth()" in _DESK


def test_layout_prefs_migrees_vers_v2() -> None:
    assert "desk-layout-v2" in _DESK
    assert "if (prefs.chat && prefs.chat > 380) delete prefs.chat" in _DESK
