"""La feuille du produit est copiee la ou le chat la cherche.

Constate le 2026-09-26 : Dockerfile.agent la copiait dans agent/static
(dans le paquet) alors que main.py monte parent.parent / "static" : la page
de l'assistant en acces direct s'affichait sans style.
"""
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[2]
_DOCKERFILE = (_RACINE / "Dockerfile.agent").read_text(encoding="utf-8")
_MAIN = (_RACINE / "agent" / "agent" / "main.py").read_text(encoding="utf-8")


def test_la_feuille_est_copiee_a_cote_du_paquet():
    assert "WORKDIR /opt/qgis-agent" in _DOCKERFILE
    assert "COPY agent/ ." in _DOCKERFILE
    assert "COPY hub/hub/static/produit.css static/produit.css" in _DOCKERFILE


def test_le_chat_la_cherche_a_cote_du_paquet():
    assert 'Path(__file__).parent.parent / "static"' in _MAIN
