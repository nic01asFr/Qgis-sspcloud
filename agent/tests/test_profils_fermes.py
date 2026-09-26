"""Filtre d'outils ferme par defaut (audit securite des acces, 2026-09-26).

Deux replis ouvraient tout :
  - `mcp_tools.allowed: []` sautait le filtre (`if ... and allowed`), et les
    profils d'assistance recevaient les 49 outils MCP, dont `execute_python`
    et `delete_file` (AG-14) ;
  - un profil absent du cache (inconnu, ou profils pas encore charges)
    recevait tous les outils.
Les deux sont desormais fermes.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent import qgis_agent as qa  # noqa: E402

OUTILS = [{"name": n, "description": "", "inputSchema": {}}
          for n in ("execute_python", "delete_file", "get_screenshot")]


class _Rep:
    def json(self):
        return {"result": {"tools": OUTILS}}


class _Client:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return _Rep()


def _outils(profil: str, cache: dict, rechargement=None) -> list[str]:
    async def _pas_de_hub():
        return 0

    with patch.object(qa, "_HUB_URL", "http://hub"), \
         patch.object(qa, "_HUB_KEY", "cle"), \
         patch.object(qa, "_PROFILES_CACHE", cache), \
         patch.object(qa.httpx, "AsyncClient", _Client), \
         patch.object(qa, "fetch_profiles_from_hub",
                      rechargement or _pas_de_hub):
        return [t["name"] for t in asyncio.run(qa._get_mcp_tools(profil))]


def test_liste_vide_veut_dire_aucun_outil():
    cache = {"component_assist": {"mcp_tools": {"allowed": []}}}
    assert _outils("component_assist", cache) == []
    with patch.object(qa, "_PROFILES_CACHE", cache):
        assert qa._get_profile_tools_whitelist("component_assist") == []


def test_all_garde_tous_les_outils():
    cache = {"standard": {"mcp_tools": {"allowed": "all"}}}
    assert _outils("standard", cache) == [o["name"] for o in OUTILS]
    with patch.object(qa, "_PROFILES_CACHE", cache):
        assert qa._get_profile_tools_whitelist("standard") is None


def test_liste_blanche_et_liste_noire():
    cache = {"p": {"mcp_tools": {"allowed": ["get_screenshot", "delete_file"],
                                 "disabled": ["delete_file"]}}}
    assert _outils("p", cache) == ["get_screenshot"]


def test_profil_inconnu_ferme():
    cache = {"standard": {"mcp_tools": {"allowed": "all"}}}
    assert _outils("profil_qui_nexiste_pas", cache) == []
    with patch.object(qa, "_PROFILES_CACHE", cache):
        assert qa._get_profile_tools_whitelist("profil_qui_nexiste_pas") == []


def test_cache_vide_recharge_puis_reste_ferme():
    """Cache vide (hub pas pret) : un rechargement est tente, puis ferme."""
    appels = []

    async def _recharge():
        appels.append(1)
        return 0

    assert _outils("standard", {}, rechargement=_recharge) == []
    assert appels == [1]


def test_cache_vide_recharge_reussi():
    cache: dict = {}

    async def _recharge():
        cache["standard"] = {"mcp_tools": {"allowed": "all"}}
        return 1

    assert _outils("standard", cache, rechargement=_recharge) == [
        o["name"] for o in OUTILS]


def test_valeur_inattendue_fermee():
    with patch.object(qa, "_PROFILES_CACHE",
                      {"p": {"mcp_tools": {"allowed": "tout"}}}):
        assert qa._get_profile_tools_whitelist("p") == []
