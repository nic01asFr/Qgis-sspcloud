"""Ce que l'outil renvoie au modele n'a pas a s'afficher tel quel.

Le pont QGIS repond par un melange : du JSON, un bloc de contexte redige
pour le modele (« --- Context: phase=... Hint: ... ») et, souvent, la
capture de la carte. Mesure du 2026-09-17 : un simple « recentre la carte
sur ma zone d'etude » affichait deux paragraphes de bruit technique avant
la phrase utile, hors du bloc que le chat sait replier.
"""
from __future__ import annotations

import asyncio
import json
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

from agent import memory          # noqa: E402
from agent import qgis_agent as qa  # noqa: E402


_IMAGE = "![carte](data:image/jpeg;base64,AAAABBBB)"
_JSON = '{"extent": [870649.15, 6231970.21, 916046.87, 6258584.70], "project_crs": "EPSG:2154"}'
_CONTEXTE = (
    "--- Context: phase=cartography | zone=Marseille | 2 layers\n"
    "    Hint: Apply a layout (apply_layout_template) then export"
)
_RETOUR_DU_PONT = f"{_JSON}\n\n{_CONTEXTE}\n\n{_IMAGE}"


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

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, methode, url, json=None, headers=None):
        if not _ClientModele.script:
            raise AssertionError("le modele a ete appele plus souvent que prevu")
        return _Flux(_ClientModele.script.pop(0))


def _appel_outil(nom: str, arguments: str) -> list[str]:
    """Les lignes SSE d'un appel d'outil, puis la fin du tour LLM."""
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
                 "function": {"name": "zoom_to", "description": "",
                              "parameters": {"type": "object", "properties": {}}}}]

    async def _outil_mcp(nom, args, username=None, **k):
        return _RETOUR_DU_PONT

    monkeypatch.setattr(qa, "_resolve_model", _modele)
    monkeypatch.setattr(qa.QGISAgent, "_build_system_prompt", _prompt)
    monkeypatch.setattr(qa.QGISAgent, "_get_tools", _outils)
    monkeypatch.setattr(qa, "_call_mcp_tool", _outil_mcp)
    monkeypatch.setattr(qa.httpx, "AsyncClient", _ClientModele)
    return qa.QGISAgent(username="user", session_id="essai-rendu",
                        profile_id="standard")


def _derouler(agent, *script: list[str]) -> list:
    _ClientModele.script = list(script)

    async def _tour():
        return [c async for c in agent.chat_stream("question", history=[])]

    return _run(_tour())


def _texte(morceaux: list) -> str:
    return "".join(m for m in morceaux if isinstance(m, str))


# ── Ce que l'utilisateur voit ────────────────────────────────────────────


def test_la_capture_reste_affichee(agent) -> None:
    rendu = _texte(_derouler(
        agent,
        _appel_outil("zoom_to", "{}"),
        _reponse_texte("La vue est centree sur Marseille."),
    ))
    assert _IMAGE in rendu


def test_le_json_et_le_contexte_partent_dans_le_bloc_technique(agent) -> None:
    """Ils restent consultables, mais dans un bloc que le chat replie."""
    rendu = _texte(_derouler(
        agent,
        _appel_outil("zoom_to", "{}"),
        _reponse_texte("La vue est centree sur Marseille."),
    ))
    # Present, mais uniquement a l'interieur d'un bloc de code.
    for morceau in ('"project_crs"', "--- Context:", "Hint:"):
        assert morceau in rendu, morceau
        avant = rendu.split(morceau)[0]
        assert avant.count("```") % 2 == 1, (
            f"{morceau} est rendu hors du bloc technique"
        )


def test_la_phrase_utile_est_bien_la(agent) -> None:
    rendu = _texte(_derouler(
        agent,
        _appel_outil("zoom_to", "{}"),
        _reponse_texte("La vue est centree sur Marseille."),
    ))
    assert "La vue est centree sur Marseille." in rendu


def test_un_retour_sans_image_reste_dans_le_bloc_technique(agent, monkeypatch) -> None:
    async def _sans_image(nom, args, username=None, **k):
        return _JSON

    monkeypatch.setattr(qa, "_call_mcp_tool", _sans_image)
    rendu = _texte(_derouler(
        agent,
        _appel_outil("zoom_to", "{}"),
        _reponse_texte("Fait."),
    ))
    avant = rendu.split('"project_crs"')[0]
    assert avant.count("```") % 2 == 1
