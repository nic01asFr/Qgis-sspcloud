"""Un modele qui cesse de repondre ne doit plus figer le tour indefiniment.

Mesure du 2026-09-17 (etude « saint martin », production) : deux tours sur
cinq sont restes bloques plus de sept minutes sur « Analyse en cours… ». Le
serveur de modeles avait cesse d'emettre sans fermer la connexion ; le read
timeout de httpx ne levait jamais, parce qu'il se rearme au moindre octet --
y compris les lignes de service intercalees entre deux paquets.

L'utilisateur n'avait alors aucune issue : ni message, ni fin de tour.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
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


def _run(coro):
    try:
        boucle = asyncio.get_event_loop()
        if boucle.is_closed():
            raise RuntimeError("boucle fermee")
    except RuntimeError:
        boucle = asyncio.new_event_loop()
        asyncio.set_event_loop(boucle)
    return boucle.run_until_complete(coro)


# ── Un serveur de modele qui se tait ─────────────────────────────────────

class _Reponse:
    """Repond selon un scenario, puis se tait sans fermer la connexion."""

    status_code = 200

    def __init__(self, lignes: list[str], silence_apres: str):
        self._lignes = lignes
        # "service" : le serveur intercale des lignes sans contenu (c'est ce
        # qui rearmait le read timeout de httpx). "total" : il ne renvoie plus
        # rien du tout, la lecture ne rend jamais la main. "non" : il conclut.
        self._silence_apres = silence_apres

    async def aiter_lines(self):
        for ligne in self._lignes:
            await asyncio.sleep(0.01)
            yield ligne
        if self._silence_apres == "service":
            while True:
                await asyncio.sleep(0.02)
                yield ":"
        if self._silence_apres == "total":
            await asyncio.sleep(3600)

    async def aread(self) -> bytes:
        return b""


class _Flux:
    def __init__(self, lignes, silence_apres):
        self._r = _Reponse(lignes, silence_apres)

    async def __aenter__(self):
        return self._r

    async def __aexit__(self, *exc):
        return False


class _ClientModele:
    lignes: list[str] = []
    silence_apres = "service"

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, methode, url, json=None, headers=None):
        return _Flux(list(_ClientModele.lignes), _ClientModele.silence_apres)


@pytest.fixture()
def agent(monkeypatch):
    async def _rien(*a, **k):
        return None

    for nom in ("add_message", "set_session_tag", "add_insight"):
        monkeypatch.setattr(memory, nom, _rien, raising=False)

    async def _modele(profil):
        return "modele-muet"

    async def _prompt(self, user_message=None):
        return "consigne systeme"

    async def _outils(self):
        return []

    monkeypatch.setattr(qa, "_resolve_model", _modele)
    monkeypatch.setattr(qa.QGISAgent, "_build_system_prompt", _prompt)
    monkeypatch.setattr(qa.QGISAgent, "_get_tools", _outils)
    monkeypatch.setattr(qa.httpx, "AsyncClient", _ClientModele)
    # Delai court : le test mesure le comportement, pas la patience.
    monkeypatch.setattr(qa, "_SILENCE_LLM_MAX", 0.3)
    return qa.QGISAgent(username="user", session_id="essai-silence",
                        profile_id="standard")


def _derouler(agent) -> list:
    async def _tour():
        return [c async for c in agent.chat_stream("question", history=[])]

    return _run(_tour())


def _paquet(**delta) -> str:
    return "data: " + json.dumps(
        {"choices": [{"delta": delta, "finish_reason": None}]},
    )


# ── Le tour s'arrete, et il le dit ───────────────────────────────────────


@pytest.mark.parametrize("mutisme", ["service", "total"])
def test_un_modele_muet_d_emblee_ne_fige_plus_le_tour(agent, mutisme) -> None:
    _ClientModele.lignes = []
    _ClientModele.silence_apres = mutisme

    with pytest.raises(RuntimeError) as leve:
        _derouler(agent)

    assert "cesse de repondre" in str(leve.value)
    assert "modele-muet" in str(leve.value)


@pytest.mark.parametrize("mutisme", ["service", "total"])
def test_le_tour_s_arrete_vite_au_lieu_d_attendre_sans_fin(agent, mutisme) -> None:
    _ClientModele.lignes = []
    _ClientModele.silence_apres = mutisme

    t0 = time.monotonic()
    with pytest.raises(RuntimeError):
        _derouler(agent)
    # Le delai est de 0.3 s dans ce test ; large marge pour la machine.
    assert time.monotonic() - t0 < 5.0


def test_un_modele_muet_en_cours_de_route_est_aussi_arrete(agent) -> None:
    """Le silence peut survenir apres un debut de reflexion : meme garde."""
    _ClientModele.lignes = [_paquet(reasoning_content="je reflechis")]
    _ClientModele.silence_apres = "total"

    with pytest.raises(RuntimeError) as leve:
        _derouler(agent)

    assert "cesse de repondre" in str(leve.value)


# ── Un modele lent mais vivant n'est pas interrompu ──────────────────────


def test_un_modele_lent_mais_qui_emet_va_jusqu_au_bout(agent) -> None:
    """Chaque paquet porteur redonne le delai plein : pas de faux positif."""
    # Vingt paquets espaces de 0.01 s : la duree totale depasse le delai de
    # 0.3 s, mais aucun silence ne l'atteint jamais.
    lignes = [_paquet(content=f"mot{i} ") for i in range(20)]
    lignes.append(
        "data: " + json.dumps(
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ),
    )
    lignes.append("data: [DONE]")
    _ClientModele.lignes = lignes
    _ClientModele.silence_apres = "non"

    morceaux = _derouler(agent)
    visible = "".join(m for m in morceaux if isinstance(m, str))
    assert "mot0" in visible and "mot19" in visible
