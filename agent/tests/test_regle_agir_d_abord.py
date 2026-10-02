"""Regle 0 des essentiels : agir d'abord, plan seulement pour le risque.

Production du 28 au 30/09 (28 tours) : l'ancienne regle « PLAN-PUIS-EXECUTE »
imposait un plan avant « TOUTE chaine de >= 2 tools AVEC IMPACT », avec un
gabarit qui finissait par « Tu veux ajuster un parametre, ou je lance avec
ces defauts ? ». Le modele l'appliquait a tout chargement : « Voici mon
plan : 1. Lister les sources… 2. Charger… », puis arret, et l'utilisateur
devait repondre « Ok », « continue », « alors ? ».
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

os.environ.setdefault("HUB_URL", "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr")

from agent import context_budget as cb  # noqa: E402
from agent import qgis_agent as qa  # noqa: E402

ESSENTIELS = qa.QGISAgent._QGIS_ESSENTIALS


def _regle_0() -> str:
    debut = ESSENTIELS.index("0. ")
    return ESSENTIELS[debut:ESSENTIELS.index("1. ", debut)]


def test_les_essentiels_tiennent_dans_leur_budget():
    assert cb.estimer_tokens(ESSENTIELS) <= cb.PLAFONDS["essentiels"] == 7_000


def test_le_gabarit_de_plan_a_valider_a_disparu():
    for gabarit in ("PLAN-PUIS-EXECUTE", "Voici mon plan :",
                    "je lance avec ces défauts", "TOUTE chaîne de >= 2 tools"):
        assert gabarit not in ESSENTIELS, gabarit


def test_la_regle_0_dit_d_agir_et_d_enchainer():
    regle = _regle_0()
    assert "AGIR D'ABORD" in regle
    for action in ("zone", "chargement", "découpage", "traitement", "style",
                   "analyse", "export dans l'étude"):
        assert action in regle, action
    assert "enchaîne les appels d'outils jusqu'au résultat" in regle
    # Les deux formes vues en production sont nommees.
    assert "Voici mon plan" in regle and "entre crochets" in regle
    # Demande vague mais executable : choix annonces en une phrase.
    assert "UNE phrase" in regle


def test_le_plan_est_reserve_aux_actions_a_risque():
    regle = _regle_0()
    sous_reserve = regle[regle.index("UNIQUEMENT pour"):]
    for risque in ("publish_*", "audience", "suppression ou écrasement",
                   "run_recipe", "GeoAI", "réellement ambigu"):
        assert risque in sous_reserve, risque
    assert "une seule question, sans plan" in regle


def test_la_regle_absolue_ne_contredit_plus_la_regle_0():
    assert "seuls les cas à risque de la règle 0 attendent un accord" in ESSENTIELS
