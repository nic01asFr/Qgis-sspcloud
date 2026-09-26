"""Layout composable desk : panels v3, navbar flex, QGIS fill."""

from __future__ import annotations

from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8"
)


def test_cadre_qgis_au_ratio_du_bureau_distant() -> None:
    """Le bureau distant a une taille fixe (Xvfb 1920x1080, x11vnc ne le
    redimensionne pas) : un cadre qui remplit la zone laissait noVNC peindre
    des bandes sombres. Le cadre prend le ratio du bureau distant (lu sur le
    canvas noVNC, 16/9 par defaut) et la plus grande taille qui tient.
    Remplace test_qgis_canvas_fill_sans_letterboxing (2026-09-10), dont le
    remplissage supposait un resizeSession que le workspace n'honore pas."""
    chunk = _DESK.split("Canvas (QGIS noVNC iframe)")[1].split("Right panel")[0]
    compact = chunk.replace(" ", "")
    assert "container-type:size" in compact
    assert "--qgis-ratio:16/9" in compact
    assert "aspect-ratio:var(--qgis-ratio,16/9)" in compact
    assert "width:min(100cqw,calc(100cqh*var(--qgis-ratio,16/9)))" in compact
    # L'iframe remplit le cadre (le cadre, lui, a le bon ratio).
    assert "width:100%;height:100%" in compact
    # Pas de fond noir letterbox autour de QGIS ; trame sous la carte.
    assert "background:#0b1220" not in _DESK
    assert "background-size:28px 28px" in chunk
    # resizeSession n'est plus demande : sans effet sur ce workspace.
    assert "resize=true" not in _DESK
    assert "function suivreRatioBureauQgis()" in _DESK
    assert "'--qgis-ratio'" in _DESK


def test_grille_defaut_sans_colonne_chat() -> None:
    """Revision 2026-09-26 : `data-resources="panel"` (Ressources en colonne,
    lot 3) n'existe plus -- la decision produit les veut en calque. Le test
    exigeait sa presence ; il verifie desormais son absence."""
    assert 'grid-template-columns:0 1fr 0 0' in _DESK
    assert 'data-chat="panel"' in _DESK
    assert 'data-resources="panel"' not in _DESK


def test_ressources_toujours_en_calque_sans_decaler_la_carte() -> None:
    """Decision produit 2026-09-26 : les Ressources s'ouvrent en calque, avec
    voile, a toutes les largeurs, sans jamais decaler la carte. Remplace
    test_ressources_colonne_si_place_sinon_calque (lot 3), qui figeait la
    colonne au-dela de 1 166 px : l'utilisateur veut l'inverse."""
    assert "function _defaultResourcesOpenMode()" in _DESK
    fn = _DESK.split("function _modeRessourcesEffectif()")[1].split("\n}")[0]
    assert "'collapsed' : 'deck'" in fn
    assert "panel" not in fn
    assert "--publi-col" not in _DESK
    assert "PUBLI_MIN" not in _DESK
    # Aucune colonne de grille pour les Ressources, chat ouvert ou non.
    assert '.desk[data-resources="deck"]{grid-template-columns:0 1fr 0 0}' in _DESK
    assert "grid-template-columns:0 1fr 6px var(--chat-w,300px)" in _DESK
    assert "var(--publi-w,240px) 1fr" not in _DESK
    # Calque fixe par-dessus le bureau, et voile.
    calque = _DESK.split('.desk[data-resources="deck"] .desk-publi{')[1].split("}")[0]
    assert "position:fixed" in calque
    assert '.desk[data-resources="deck"] .desk-backdrop' in _DESK
    assert '[data-chat="panel"] .desk-canvas' in _DESK


def test_chat_retreci_sans_perdre_la_largeur_choisie() -> None:
    """Le chat cede la place a la carte sans que sa largeur choisie change."""
    assert "function _largeurChatMax(vw)" in _DESK
    clamp = _DESK.split("function clampLayoutToViewport(vw)")[1].split("function updateLayout()")[0]
    assert "_largeurChatMax(vw)" in clamp
    assert "largeurChatChoisie =" not in clamp
    assert "layoutState.chat.width = largeurChatChoisie ||" in _DESK
    assert "Math.min(600, _largeurChatMax(window.innerWidth), startW - delta)" in _DESK


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


def test_modes_css_ressources_calque_chat_colonne() -> None:
    """Le mode calque du chat (`data-chat="deck"`, voile compris) est retire
    le 2026-09-26 : le chat decale la carte, il ne la recouvre jamais."""
    assert '[data-resources="deck"]' in _DESK
    assert '[data-chat="deck"]' not in _DESK
    assert '[data-chat="collapsed"]' in _DESK
