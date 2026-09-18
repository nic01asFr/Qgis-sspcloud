"""L'endpoint de mise a jour doit S'EXECUTER, pas seulement bien se lire.

Tous les tests du bandeau lisaient le source. Il etait coherent, et pourtant
le bouton repondait « HTTP 401 » a chaque clic : l'endpoint levait un
`NameError: name '_version' is not defined` -- le module n'est importe que
localement, dans la route `/version`.

Le 401 etait trompeur. Le middleware enveloppe `call_next` dans un
`try/except Exception` destine a la validation du cookie : toute erreur de
l'endpoint y tombe et ressort en « Auth requise ». Seule la lecture des
journaux du pod donnait la vraie cause.

Ces tests appellent donc la fonction pour de vrai.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from hub import main as hub_main


class _RequeteSimulee:
    """Le minimum dont l'endpoint se sert : un corps JSON."""

    def __init__(self, corps: dict | None):
        self._corps = corps

    async def json(self):
        if self._corps is None:
            raise ValueError("pas de corps")
        return self._corps


def _appeler(corps: dict | None):
    return asyncio.run(hub_main.mettre_a_jour_les_briques(_RequeteSimulee(corps)))


@pytest.fixture(autouse=True)
def _rien_ne_redemarre(monkeypatch):
    """Aucun test ne doit toucher au cluster."""
    redemarres: list[str] = []
    monkeypatch.setattr(hub_main.sessions, "kubectl_rollout_restart",
                        lambda nom: (redemarres.append(nom), (True, ""))[1])
    return redemarres


def test_l_endpoint_s_execute_sans_exploser(monkeypatch):
    """Le defaut qu'aucun test de source ne pouvait voir."""
    from hub import version as v
    monkeypatch.setattr(v, "etat", lambda: {"mise_a_jour_disponible": []})

    reponse = _appeler({})

    assert reponse["redemarre"] == []
    assert "deja a jour" in reponse["message"]


def test_un_corps_absent_est_tolere(monkeypatch):
    """Le navigateur peut poster sans corps : ce n'est pas une erreur."""
    from hub import version as v
    monkeypatch.setattr(v, "etat", lambda: {"mise_a_jour_disponible": []})

    reponse = _appeler(None)

    assert reponse["redemarre"] == []


def test_une_brique_inconnue_est_refusee_proprement():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as leve:
        _appeler({"briques": ["chose"]})
    assert leve.value.status_code == 400
    assert "chose" in str(leve.value.detail)


def test_l_agent_est_redemarre_quand_on_le_demande(_rien_ne_redemarre, monkeypatch):
    from hub import version as v
    oublis: list[bool] = []
    monkeypatch.setattr(v, "oublier_le_releve", lambda: oublis.append(True))

    reponse = _appeler({"briques": ["agent"]})

    assert _rien_ne_redemarre == ["qgis-agent"]
    assert reponse["redemarre"][0]["brique"] == "agent"
    assert reponse["redemarre"][0]["redemarre"] is True
    # Et le releve est oublie, sinon /version rendrait l'etat d'avant.
    assert oublis == [True]


def test_sans_briques_demandees_on_prend_celles_qui_sont_en_retard(
    _rien_ne_redemarre, monkeypatch,
):
    from hub import version as v
    monkeypatch.setattr(v, "etat", lambda: {"mise_a_jour_disponible": ["agent"]})
    monkeypatch.setattr(v, "oublier_le_releve", lambda: None)

    reponse = _appeler({})

    assert _rien_ne_redemarre == ["qgis-agent"]
    assert [f["brique"] for f in reponse["redemarre"]] == ["agent"]


def test_la_reponse_est_serialisable(_rien_ne_redemarre, monkeypatch):
    """Elle part en JSON vers le navigateur."""
    from hub import version as v
    monkeypatch.setattr(v, "oublier_le_releve", lambda: None)

    reponse = _appeler({"briques": ["agent"]})

    json.dumps(reponse)
