"""Verrouille le redimensionnement manuel du panneau chat dans desk.html."""

from __future__ import annotations

from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8"
)


def test_resize_chat_verrouille_update_layout_pendant_drag() -> None:
    assert "let _redimensionneChat = false" in _DESK
    assert "if (_redimensionneChat) return" in _DESK
    assert "if (!_redimensionneChat) updateLayout()" in _DESK


def test_resize_chat_utilise_pointer_events() -> None:
    assert "pointerdown" in _DESK
    assert "setPointerCapture" in _DESK
    assert "if (isChat) _redimensionneChat = true" in _DESK


def test_resize_chat_met_a_jour_largeur_choisie_pendant_drag() -> None:
    assert "if (isChat) largeurChatChoisie = newW" in _DESK
