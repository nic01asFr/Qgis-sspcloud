"""Le raisonnement de qwen3 ne doit plus vider le tour.

Mesure en production (28-30/09) : 7 tours sur 37 epuisaient le plafond de
4 096 jetons a reflechir, sans action ni reponse. Correctif : plafond releve,
et relance / recapitulatif / conclusion sans raisonnement.
"""
from pathlib import Path

_SRC = (Path(__file__).resolve().parents[1] / "agent" / "qgis_agent.py").read_text(encoding="utf-8")


def test_plafond_releve_et_reglable():
    assert '_MAX_TOKENS_TOUR = int(os.getenv("AGENT_MAX_TOKENS", "12288"))' in _SRC
    assert '"max_tokens": _MAX_TOKENS_TOUR' in _SRC
    assert '"max_tokens": 4096' not in _SRC


def test_relance_apres_budget_epuise_sans_reflexion():
    bloc = _SRC.split("Budget epuise par la reflexion")[1][:600]
    assert "couper_reflexion = True" in _SRC.split("Budget epuise par la reflexion")[0][-400:] + bloc
    assert "payload.update(_SANS_REFLEXION)" in _SRC


def test_recap_et_conclusion_sans_reflexion():
    assert _SRC.count("**_SANS_REFLEXION") == 2
    assert '"enable_thinking": False' in _SRC
