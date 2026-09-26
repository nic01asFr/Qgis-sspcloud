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
    assert 'grid-template-columns:0 1fr 0 0' in _DESK
    assert 'data-chat="panel"' in _DESK
    assert 'data-resources="panel"' in _DESK


def test_ressources_colonne_si_place_sinon_calque() -> None:
    """Regles de priorite 2026-09-26 : la carte garde 640 px ; les Ressources
    prennent une colonne quand la place le permet, sinon un calque. L'etat
    memorise reste 'deck' (ouvert) / 'collapsed' ; le rendu decide.
    Remplace test_ressources_ne_decalent_pas_la_carte : en calque permanent,
    Ressources + chat ne laissaient qu'une bande de carte d'environ 380 px,
    voilee et inutilisable."""
    assert "return 'deck';" in _DESK
    assert "function _defaultResourcesOpenMode()" in _DESK
    assert "const CARTE_MIN_W = 640" in _DESK
    assert "const PUBLI_MIN = 300" in _DESK
    assert "function _modeRessourcesEffectif(vw)" in _DESK
    assert "const r = _modeRessourcesEffectif(window.innerWidth);" in _DESK
    # Colonne : largeur calculee, jamais la variable brute du panneau.
    assert '.desk[data-resources="panel"]{grid-template-columns:var(--publi-col,300px) 1fr 0 0}' in _DESK
    assert "var(--publi-col,300px) 1fr 6px var(--chat-w,300px)" in _DESK
    assert "var(--publi-w,240px) 1fr" not in _DESK
    # Voile seulement pour le calque, pas pour la colonne.
    assert '.desk[data-resources="panel"] .desk-backdrop' not in _DESK
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


def test_modes_deck_css() -> None:
    assert '[data-resources="deck"]' in _DESK
    assert '[data-chat="deck"]' in _DESK
    assert '[data-chat="collapsed"]' in _DESK
