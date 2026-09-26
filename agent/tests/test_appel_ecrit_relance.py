"""Un appel d'outil ecrit en texte relance le modele une fois (D1).

Live du 2026-09-26 : le modele a repondu `> **`get_project_info`** — pour
récupérer le layer_id…` au lieu d'emettre un appel. Le tour s'est clos a
iter=0 sans action, la bulle est restee sur « Rédaction de la réponse… 5 s ».

On deroule la vraie `chat_stream` contre un modele simule, comme
test_budget_resultat_outil_boucle.py.
"""
from __future__ import annotations

import asyncio
import copy
import json
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
from agent import texte_modele         # noqa: E402

_FAUX_APPEL = "> **`get_project_info`** — pour récupérer le layer_id de la couche bâti"


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
    status_code = 200

    def __init__(self, lignes):
        self._lignes = lignes

    async def aiter_lines(self):
        for ligne in self._lignes:
            yield ligne

    async def aread(self) -> bytes:
        return b""


class _Flux:
    def __init__(self, lignes):
        self._r = _Reponse(lignes)

    async def __aenter__(self):
        return self._r

    async def __aexit__(self, *exc):
        return False


class _ClientModele:
    script: list[list[str]] = []
    envois: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, methode, url, json=None, headers=None):
        _ClientModele.envois.append(copy.deepcopy(json))
        if not _ClientModele.script:
            raise AssertionError("le modele a ete appele plus souvent que prevu")
        return _Flux(_ClientModele.script.pop(0))


def _appel_outil(nom: str, arguments: str = "{}") -> list[str]:
    delta = {"tool_calls": [{"index": 0, "id": "appel-1",
                             "function": {"name": nom, "arguments": arguments}}]}
    return [
        "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": None}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]


def _reponse_texte(texte: str, reflexion: str = "") -> list[str]:
    lignes = []
    if reflexion:
        lignes.append("data: " + json.dumps({"choices": [{
            "delta": {"reasoning_content": reflexion}, "finish_reason": None}]}))
    lignes += [
        "data: " + json.dumps({"choices": [{"delta": {"content": texte},
                                            "finish_reason": None}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    return lignes


@pytest.fixture()
def agent(monkeypatch):
    persistes: list[str] = []

    async def _rien(*a, **k):
        return None

    async def _persister(session_id, role, content, tool_calls=None):
        if role == "assistant":
            persistes.append(content)

    for nom in ("set_session_tag", "add_insight"):
        monkeypatch.setattr(memory, nom, _rien, raising=False)
    monkeypatch.setattr(memory, "add_message", _persister)

    async def _modele(profil):
        return "modele-essai"

    async def _prompt(self, user_message=None):
        return "consigne systeme"

    async def _outils(self):
        return [{"type": "function",
                 "function": {"name": nom, "description": "",
                              "parameters": {"type": "object", "properties": {}}}}
                for nom in ("get_project_info", "clip_to_study_zone")]

    async def _outil_mcp(nom, args, username=None, **k):
        return '{"success": true, "layers": [{"id": "batiments_3f2a"}]}'

    monkeypatch.setattr(qa, "_resolve_model", _modele)
    monkeypatch.setattr(qa.QGISAgent, "_build_system_prompt", _prompt)
    monkeypatch.setattr(qa.QGISAgent, "_get_tools", _outils)
    monkeypatch.setattr(qa, "_call_mcp_tool", _outil_mcp)
    monkeypatch.setattr(qa, "_append_history", _rien)
    monkeypatch.setattr(qa.httpx, "AsyncClient", _ClientModele)
    _ClientModele.envois = []
    a = qa.QGISAgent(username="user", session_id="essai-appel-ecrit",
                     profile_id="standard")
    a.persistes = persistes
    return a


def _tour(agent, script, history=None) -> list:
    _ClientModele.script = script

    async def _go():
        return [c async for c in agent.chat_stream("uniquement le bâti dans la commune",
                                                   history=history or [])]

    return _run(_go())


def _texte(evenements) -> str:
    return "".join(e for e in evenements if isinstance(e, str))


def test_un_appel_ecrit_relance_le_modele_une_fois(agent):
    evenements = _tour(agent, [
        _reponse_texte(_FAUX_APPEL, reflexion="il me faut le layer_id"),
        _appel_outil("get_project_info"),
        _reponse_texte("La couche bâti est prête."),
    ])
    assert len(_ClientModele.envois) == 3, "une relance, puis la suite normale"
    relance = _ClientModele.envois[1]["messages"]
    assert relance[-1] == {"role": "system", "content": qa._CONSIGNE_APPEL_ECRIT}
    assert relance[-2] == {"role": "assistant", "content": _FAUX_APPEL}
    # Le faux appel est retire de l'affichage et de la persistance. Le
    # retrait porte sur le texte AFFICHE, deja passe par le garde-fou de
    # sortie (lot 3) : c'est lui que le chat doit retrouver en fin de bulle.
    affiche = texte_modele.humaniser(_FAUX_APPEL, {"get_project_info"})
    assert "get_project_info" not in affiche
    assert {"retirer_texte": affiche} in evenements
    assert _FAUX_APPEL not in agent.persistes[-1]
    assert "La couche bâti est prête." in agent.persistes[-1]


def test_la_relance_n_a_lieu_qu_une_fois_et_le_tour_finit_lisible(agent):
    evenements = _tour(agent, [
        _reponse_texte(_FAUX_APPEL),
        _reponse_texte(_FAUX_APPEL),
    ])
    assert len(_ClientModele.envois) == 2, "pas de seconde relance"
    assert qa.MESSAGE_TOUR_SANS_REPONSE in _texte(evenements)
    persiste = agent.persistes[-1]
    assert _FAUX_APPEL not in persiste
    assert texte_modele.texte_final(persiste) == qa.MESSAGE_TOUR_SANS_REPONSE


def test_une_reponse_normale_ne_relance_pas(agent):
    evenements = _tour(agent, [
        _reponse_texte("J'ai découpé avec clip_to_study_zone : 54 557 bâtiments."),
    ])
    assert len(_ClientModele.envois) == 1
    assert not any(isinstance(e, dict) and "retirer_texte" in e for e in evenements)
    assert qa.MESSAGE_TOUR_SANS_REPONSE not in _texte(evenements)


def test_un_tour_sans_texte_visible_se_termine_par_un_message(agent, monkeypatch):
    """Outils executes, recapitulatif force muet : rien de lisible sans le
    garde-fou final (la ligne d'outil est repliee dans le chat)."""
    evenements = _tour(agent, [
        _appel_outil("get_project_info"),
        _reponse_texte("", reflexion="je conclus"),
        ["data: [DONE]"],  # recapitulatif force : le modele ne dit rien
    ])
    assert qa.MESSAGE_TOUR_SANS_REPONSE in _texte(evenements)
    assert texte_modele.texte_final(agent.persistes[-1]) == qa.MESSAGE_TOUR_SANS_REPONSE


def test_les_emojis_du_modele_ne_parviennent_pas_a_l_utilisateur(agent):
    evenements = _tour(agent, [_reponse_texte("\u26A0\uFE0F 54 557 bâtiments \U0001F7E0 dans la commune.")])
    assert "54 557 bâtiments dans la commune." in _texte(evenements)
    assert "\u26A0" not in agent.persistes[-1] and "\U0001F7E0" not in agent.persistes[-1]


def test_l_historique_relu_par_le_modele_est_nettoye(agent):
    stocke = ('<details class="agent-reasoning"><summary>Raisonnement</summary>\n\n'
              "x\n\n</details>\n\nZone définie.\n\n> **`set_study_zone`** — `target=Aix`\n")
    _tour(agent, [_reponse_texte("D'accord.")], history=[
        {"role": "user", "content": "Aix"},
        {"role": "assistant", "content": stocke},
    ])
    envoye = _ClientModele.envois[0]["messages"]
    assistant = [m for m in envoye if m["role"] == "assistant"]
    assert assistant == [{"role": "assistant", "content": "Zone définie."}]
