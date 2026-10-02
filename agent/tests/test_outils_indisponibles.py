"""Outils du hub indisponibles : nouvel essai, derniere liste valide, arret clair.

Production du 28-30/09 : `WARNING: MCP tools non récupérés:` puis
`fetch_profiles : /profiles HTTP 503` (hub en redemarrage). `_get_mcp_tools`
rendait une liste vide sans retenter ; le tour est parti au modele avec 2
outils natifs sur 36 (`outils exposes … n=2/36`), qui n'a pu que decrire.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

os.environ.setdefault("HUB_URL", "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr")

from agent import memory               # noqa: E402
from agent import qgis_agent as qa     # noqa: E402

OUTILS_HUB = [{"name": n, "description": "", "inputSchema": {"type": "object"}}
              for n in ("set_study_zone", "smart_load", "clip_to_study_zone")]


def _run(coro):
    try:
        boucle = asyncio.get_event_loop()
        if boucle.is_closed():
            raise RuntimeError("boucle fermee")
    except RuntimeError:
        boucle = asyncio.new_event_loop()
        asyncio.set_event_loop(boucle)
    return boucle.run_until_complete(coro)


class _Reponse:
    def __init__(self, statut: int, data):
        self.status_code = statut
        self._data = data

    def json(self):
        return self._data


class _Hub:
    """Faux client HTTP : rejoue un script de reponses a `tools/list`.

    Un element du script est une exception (levee), un code HTTP d'erreur,
    ou une liste d'outils. Tout appel au modele est une erreur de test.
    """
    script: list = []
    appels = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None, **k):
        assert url.endswith("/mcp") and json["method"] == "tools/list"
        _Hub.appels += 1
        suivant = _Hub.script.pop(0)
        if isinstance(suivant, Exception):
            raise suivant
        if isinstance(suivant, int):
            return _Reponse(suivant, {"detail": "Service Unavailable"})
        return _Reponse(200, {"result": {"tools": suivant}})

    def stream(self, *a, **k):
        raise AssertionError("le modele ne doit pas etre appele sans outils")


@pytest.fixture(autouse=True)
def hub(monkeypatch):
    attentes: list[float] = []

    async def _dormir(s):
        attentes.append(s)

    monkeypatch.setattr(qa, "_HUB_URL", "https://hub.test")
    monkeypatch.setattr(qa, "_HUB_KEY", "cle")
    monkeypatch.setattr(qa.httpx, "AsyncClient", _Hub)
    monkeypatch.setattr(qa.asyncio, "sleep", _dormir)
    monkeypatch.setattr(qa, "_DERNIERS_OUTILS_MCP", {})
    monkeypatch.setattr(qa, "_PROFILES_CACHE", {})
    _Hub.appels = 0
    _Hub.script = []
    return attentes


def _noms(outils):
    return [o["name"] for o in outils]


def test_un_echec_puis_un_succes_rend_la_liste(hub):
    _Hub.script = [RuntimeError("connexion refusee"), OUTILS_HUB]
    assert _noms(_run(qa._get_mcp_tools("standard"))) == _noms(OUTILS_HUB)
    assert _Hub.appels == 2
    assert hub == [qa._DELAI_ESSAI_OUTILS_S], "un delai court entre les essais"


@pytest.mark.parametrize("echec", [503, [], RuntimeError("timeout")])
def test_http_503_liste_vide_ou_exception_comptent_comme_echecs(echec):
    _Hub.script = [echec, echec]
    with pytest.raises(qa.OutilsIndisponibles):
        _run(qa._get_mcp_tools("standard"))
    assert _Hub.appels == qa._ESSAIS_OUTILS == 2


def test_la_derniere_liste_valide_du_profil_sert_en_cas_d_echec(caplog):
    _Hub.script = [OUTILS_HUB]
    _run(qa._get_mcp_tools("standard"))
    _Hub.script = [503, RuntimeError("timeout")]
    with caplog.at_level("WARNING", logger="agent.qgis_agent"):
        outils = _run(qa._get_mcp_tools("standard"))
    assert _noms(outils) == _noms(OUTILS_HUB)
    assert any("derniere liste valide reutilisee" in r.getMessage()
               and "age=" in r.getMessage() for r in caplog.records)


def test_le_cache_est_par_profil(caplog):
    _Hub.script = [OUTILS_HUB]
    _run(qa._get_mcp_tools("standard"))
    _Hub.script = [503, 503]
    with pytest.raises(qa.OutilsIndisponibles):
        _run(qa._get_mcp_tools("guided_tour"))


def test_une_liste_trop_ancienne_ne_sert_plus(monkeypatch):
    _Hub.script = [OUTILS_HUB]
    _run(qa._get_mcp_tools("standard"))
    horodatage, liste = qa._DERNIERS_OUTILS_MCP["standard"]
    qa._DERNIERS_OUTILS_MCP["standard"] = (horodatage - qa._AGE_MAX_OUTILS_S - 1, liste)
    _Hub.script = [503, 503]
    with pytest.raises(qa.OutilsIndisponibles):
        _run(qa._get_mcp_tools("standard"))


def test_sans_outils_le_tour_ne_part_pas_au_modele(monkeypatch, caplog):
    persistes: list[str] = []

    async def _persister(session_id, role, content, tool_calls=None):
        persistes.append((role, content))

    async def _prompt(self, user_message=None):
        raise AssertionError("le prompt n'a pas a etre construit sans outils")

    monkeypatch.setattr(memory, "add_message", _persister)
    monkeypatch.setattr(qa.QGISAgent, "_build_system_prompt", _prompt)
    _Hub.script = [503, 503]
    agent = qa.QGISAgent(username="user", session_id="essai-sans-outils",
                         profile_id="standard")

    async def _go():
        return [c async for c in agent.chat_stream(
            "Affiche les trames vertes et bleues et les réseaux sur Rousset")]

    with caplog.at_level("ERROR", logger="agent.qgis_agent"):
        evenements = _run(_go())
    assert evenements == [qa.MESSAGE_OUTILS_INDISPONIBLES]
    assert persistes[-1] == ("assistant", qa.MESSAGE_OUTILS_INDISPONIBLES)
    assert "momentanément indisponibles" in qa.MESSAGE_OUTILS_INDISPONIBLES
    assert any("outils QGIS indisponibles" in r.getMessage() for r in caplog.records)
    # Le tour suivant retente : l'echec n'est pas mis en cache sur l'agent.
    assert agent._tools_cache is None
