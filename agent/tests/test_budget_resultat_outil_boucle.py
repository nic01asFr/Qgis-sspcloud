"""La boucle d'outils passe bien le resultat par le budget avant le LLM.

Complement de test_budget_resultat_outil.py (fonction pure) : on deroule un
tour complet avec un `execute_python` qui renvoie ~90 000 caracteres de
stdout et on lit le message `tool` reellement envoye au modele au second
appel. Avant ce chantier, il partait en entier.
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

# qgis_agent capture _HUB_URL une seule fois au chargement : meme valeur
# que les autres tests, sinon l'ordre de collecte les ferait diverger.
os.environ.setdefault("HUB_URL", "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr")

from agent import memory               # noqa: E402
from agent import qgis_agent as qa     # noqa: E402
from agent import tool_result_budget   # noqa: E402

_CONTEXTE = (
    "\n--- Context: phase=analysis | zone=Aix-en-Provence | 2 layers\n"
    "    Hint: Compte apres decoupe au contour"
)
_GROS_RETOUR = json.dumps({
    "success": True,
    "result": {"total": 20_000},
    "stdout": "\n".join(f"parcelle {i} traitée" for i in range(6000)),
}, indent=2) + _CONTEXTE


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
        # Copie : la boucle continue de muter la meme liste de messages.
        _ClientModele.envois.append(copy.deepcopy(json))
        if not _ClientModele.script:
            raise AssertionError("le modele a ete appele plus souvent que prevu")
        return _Flux(_ClientModele.script.pop(0))


def _appel_outil(nom: str, arguments: str) -> list[str]:
    delta = {"tool_calls": [{"index": 0, "id": "appel-1",
                             "function": {"name": nom, "arguments": arguments}}]}
    return [
        "data: " + json.dumps({"choices": [{"delta": delta,
                                            "finish_reason": None}]}),
        "data: " + json.dumps({"choices": [{"delta": {},
                                            "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]


def _reponse_texte(texte: str) -> list[str]:
    return [
        "data: " + json.dumps({"choices": [{"delta": {"content": texte},
                                            "finish_reason": None}]}),
        "data: " + json.dumps({"choices": [{"delta": {},
                                            "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]


@pytest.fixture()
def agent(monkeypatch):
    async def _rien(*a, **k):
        return None

    for nom in ("add_message", "set_session_tag", "add_insight"):
        monkeypatch.setattr(memory, nom, _rien, raising=False)

    async def _modele(profil):
        return "modele-essai"

    async def _prompt(self, user_message=None):
        return "consigne systeme"

    async def _outils(self):
        return [{"type": "function",
                 "function": {"name": "execute_python", "description": "",
                              "parameters": {"type": "object", "properties": {}}}}]

    async def _outil_mcp(nom, args, username=None, **k):
        return _GROS_RETOUR

    monkeypatch.setattr(qa, "_resolve_model", _modele)
    monkeypatch.setattr(qa.QGISAgent, "_build_system_prompt", _prompt)
    monkeypatch.setattr(qa.QGISAgent, "_get_tools", _outils)
    monkeypatch.setattr(qa, "_call_mcp_tool", _outil_mcp)
    monkeypatch.setattr(qa, "_append_history", _rien)
    monkeypatch.setattr(qa.httpx, "AsyncClient", _ClientModele)
    monkeypatch.delenv("TOOL_RESULT_BUDGET_CHARS", raising=False)
    monkeypatch.delenv("TOOL_RESULT_BUDGET_EXECUTE_PYTHON", raising=False)
    _ClientModele.envois = []
    return qa.QGISAgent(username="user", session_id="essai-budget",
                        profile_id="standard")


def _derouler(agent) -> list:
    _ClientModele.script = [
        _appel_outil("execute_python", '{"code": "print(1)"}'),
        _reponse_texte("Les 20 000 parcelles sont traitees."),
    ]

    async def _tour():
        return [c async for c in agent.chat_stream("question", history=[])]

    return _run(_tour())


def _message_outil_envoye() -> str:
    assert len(_ClientModele.envois) >= 2, "le modele n'a pas ete rappele"
    messages = _ClientModele.envois[1]["messages"]
    outils = [m for m in messages if m.get("role") == "tool"]
    assert len(outils) == 1
    return outils[0]["content"]


def test_le_resultat_envoye_au_modele_est_borne(agent) -> None:
    assert len(_GROS_RETOUR) > 50_000
    _derouler(agent)
    contenu = _message_outil_envoye()
    assert len(contenu) <= 8000
    assert tool_result_budget.SENTINELLE in contenu
    assert contenu.endswith(_CONTEXTE)
    assert '"success": true' in contenu


def test_la_boucle_appelle_la_fonction_de_budget(agent, monkeypatch) -> None:
    vus: list[tuple[int, str]] = []

    def _espion(texte, nom_outil, budget=None):
        vus.append((len(texte), nom_outil))
        return "ABREGE"

    monkeypatch.setattr(tool_result_budget, "abreger_resultat_outil", _espion)
    _derouler(agent)
    assert vus == [(len(_GROS_RETOUR), "execute_python")]
    assert _message_outil_envoye() == "ABREGE"


def test_budget_desactivable_par_env(agent, monkeypatch) -> None:
    monkeypatch.setenv("TOOL_RESULT_BUDGET_EXECUTE_PYTHON", "0")
    _derouler(agent)
    assert _message_outil_envoye() == _GROS_RETOUR
