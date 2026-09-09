"""Deck bureau : onglets Carte / Sources / Livrables."""

from __future__ import annotations

from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8"
)


def test_deck_tabs_html() -> None:
    assert 'class="deck-tabs"' in _DESK
    assert 'data-deck-tab="carte"' in _DESK
    assert 'data-deck-tab="sources"' in _DESK
    assert 'data-deck-tab="livrables"' in _DESK
    assert 'id="desk-deck-sources-host"' in _DESK
    assert 'id="desk-deck-livrables-host"' in _DESK
    assert 'id="publi-rpanes-host"' in _DESK


def test_deck_view_persiste_layout_v3() -> None:
    assert "deckView" in _DESK
    assert "function setDeckView(" in _DESK
    assert "desk.dataset.deckView" in _DESK


def test_relocation_rpanes_deck() -> None:
    assert "function _mountRpane(" in _DESK
    assert "function _remountRpanesToPubli(" in _DESK
    assert "function _deckHostEl(" in _DESK
    assert "const _deckHosts" not in _DESK


def test_deck_init_apres_loaders() -> None:
    assert "initDeckViewFromLayout" in _DESK
    loaders = _DESK.index("async function loadSources()")
    init = _DESK.index("function initDeckViewFromLayout()")
    assert init > loaders


def test_shift_clic_cycle_panel_deck() -> None:
    assert "function _cyclePanelMode(" in _DESK
    assert "opts && opts.shiftKey" in _DESK


def test_ouvrir_ressources_quitte_vue_deck() -> None:
    chunk = _DESK.split("function togglePanel(")[1].split("document.getElementById('toggle-publi')")[0]
    assert "layoutState.deckView !== 'carte'" in chunk
    assert "_remountRpanesToPubli()" in chunk
