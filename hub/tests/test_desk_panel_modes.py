"""Layout composable desk : panels v3, navbar flex, QGIS fill."""

from __future__ import annotations

from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8"
)


def test_qgis_canvas_fill_sans_letterboxing() -> None:
    chunk = _DESK.split("Canvas (QGIS noVNC iframe)")[1].split("Right panel")[0]
    assert "aspect-ratio:16/9" not in chunk
    assert "place-items:center" not in chunk
    assert "width:100%;height:100%" in chunk.replace(" ", "")


def test_grille_defaut_sans_colonne_chat() -> None:
    assert 'grid-template-columns:0 1fr 0 0' in _DESK
    assert 'data-chat="panel"' in _DESK
    assert 'data-resources="panel"' in _DESK


def test_ressources_ne_decalent_pas_la_carte() -> None:
    """Seul le chat panel réduit la grille ; ressources = calque."""
    assert "return 'deck';" in _DESK
    assert "function _defaultResourcesOpenMode()" in _DESK
    # Plus de colonne publi qui pousse le canvas
    assert "var(--publi-w,240px) 1fr 0 0" not in _DESK
    assert "var(--publi-w,240px) 1fr 6px" not in _DESK
    assert '[data-chat="panel"] .desk-canvas' in _DESK
    assert "background-size:28px 28px" in _DESK
    # Pas de fond noir letterbox autour de QGIS
    assert "background:#0b1220" not in _DESK
    assert "resize=true" in _DESK


def test_layout_v3_persiste() -> None:
    assert "desk-layout-v3" in _DESK
    assert "function applyPanelState()" in _DESK
    assert "function _loadLayoutState()" in _DESK


def test_navbar_flex_toggles_fixes() -> None:
    assert 'class="desk-nav"' in _DESK
    assert "desk-nav__actions" in _DESK
    assert "right:calc(var(--chat-w" not in _DESK
    assert 'aria-controls="desk-chat-panel"' in _DESK
    assert 'aria-controls="desk-publi-panel"' in _DESK


def test_modes_deck_css() -> None:
    assert '[data-resources="deck"]' in _DESK
    assert '[data-chat="deck"]' in _DESK
    assert '[data-chat="collapsed"]' in _DESK
