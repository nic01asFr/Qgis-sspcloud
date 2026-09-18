"""QGIS occupe n'est pas une panne du hub.

Mesure du 2026-09-17 : chaque gel de QGIS produisait « Checkpoint refusé par
hub (500) » dans les journaux de l'agent. Le snapshot pre-tool s'accorde 15 s ;
quand un traitement long occupe le thread principal de QGIS, il ne peut pas
aboutir. Le hub repondait 500, ce qui fait chercher un bug chez lui alors que
la cause est ailleurs -- et l'agent poursuit de toute facon, simplement sans
point de retour pour ce tool.
"""
from __future__ import annotations

from pathlib import Path

_RACINE = Path(__file__).resolve().parents[1]
_HUB = (_RACINE / "hub" / "main.py").read_text(encoding="utf-8")
_AGENT = (_RACINE.parent / "agent" / "agent" / "qgis_agent.py").read_text(
    encoding="utf-8")


def _bloc_checkpoint() -> str:
    return _HUB.split("async def session_checkpoint")[1].split("\n@app.")[0]


def test_un_qgis_occupe_repond_503_et_non_500():
    bloc = _bloc_checkpoint()
    assert "httpx.TimeoutException, asyncio.TimeoutError" in bloc
    assert 'HTTPException(503, "QGIS occupé : snapshot non pris")' in bloc


def test_une_vraie_panne_reste_un_500():
    """On distingue, on ne masque pas : tout le reste reste une erreur."""
    bloc = _bloc_checkpoint()
    assert 'HTTPException(500, f"Snapshot échec: {exc}")' in bloc


def test_l_agent_ne_crie_pas_au_refus_quand_qgis_est_occupe():
    bloc = _AGENT.split("Hook checkpoint pré-mutating")[1].split("log.info(\"Tool call:")[0]
    assert "ck_resp.status_code == 503" in bloc
    assert "Pas de point de retour avant" in bloc
    # Les autres codes restent un avertissement.
    assert 'log.warning("Checkpoint refusé par hub (%d)"' in bloc
