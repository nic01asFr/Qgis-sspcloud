"""Le pied de page de « Mon espace » reste en bas de l'ecran.

Signale le 2026-10-02 : sur une page courte, le pied s'arretait au contenu
et laissait une bande vide dessous.
"""
from pathlib import Path

_WS = (Path(__file__).resolve().parents[1] / "templates" / "workspace.html").read_text(encoding="utf-8")


def test_corps_en_colonne_sur_toute_la_hauteur():
    assert "body{min-height:100vh;min-height:100dvh;display:flex;flex-direction:column}" in _WS


def test_la_zone_principale_prend_l_espace_restant():
    assert ".ws-main{padding:14px 0 32px;flex:1 0 auto}" in _WS
    assert _WS.index('<main role="main" class="ws-main">') < _WS.index('<footer class="qs-pied"')
