"""Authentification hub -> workspace (audit securite des acces, SEC-2).

Le workspace (API 8080, MCP 8100, noVNC 6080) n'authentifiait aucun appel.
Le hub s'authentifie desormais sur tout ce qu'il relaie. Le calcul du jeton
doit rester identique a celui de BigQgisMCP `src/workspace_auth.py` : le
vecteur de reference ci-dessous est le meme dans les deux depots.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from hub import main, sessions, workspace_auth  # noqa: E402

# Vecteur partage avec BigQgisMCP tests/test_workspace_auth.py.
CLE_REFERENCE = "qgis_alice_0123456789abcdef0123456789abcdef"
JETON_REFERENCE = hmac.new(
    CLE_REFERENCE.encode(), b"qgis-workspace-v1", hashlib.sha256).hexdigest()


@pytest.fixture
def cle(monkeypatch):
    monkeypatch.setenv("HUB_API_KEY", CLE_REFERENCE)
    monkeypatch.delenv("WORKSPACE_TOKEN", raising=False)


def test_jeton_derive(cle):
    assert workspace_auth.jeton_workspace() == JETON_REFERENCE
    assert CLE_REFERENCE not in workspace_auth.jeton_workspace()
    assert workspace_auth.entetes_workspace() == {"X-Workspace-Token": JETON_REFERENCE}


def test_jeton_explicite_prioritaire(cle, monkeypatch):
    monkeypatch.setenv("WORKSPACE_TOKEN", "jeton-fourni")
    assert workspace_auth.jeton_workspace() == "jeton-fourni"


def test_sans_cle_aucun_entete(monkeypatch):
    monkeypatch.delenv("HUB_API_KEY", raising=False)
    monkeypatch.delenv("WORKSPACE_TOKEN", raising=False)
    assert workspace_auth.entetes_workspace() == {}
    assert workspace_auth.entete_basic_vnc() == {}


def test_entete_basic_vnc(cle):
    valeur = workspace_auth.entete_basic_vnc()["Authorization"]
    assert valeur.startswith("Basic ")
    assert base64.b64decode(valeur[6:]).decode() == f"hub:{JETON_REFERENCE}"


class _Req:
    method = "POST"

    def __init__(self, entetes):
        self.headers = entetes
        self.query_params = {}

    async def body(self):
        return b""


def test_proxy_ajoute_le_jeton_et_ecrase_celui_du_client(cle, monkeypatch):
    vus = {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def build_request(self, methode, url, headers=None, **k):
            vus.update(headers or {})
            return object()

        async def send(self, *a, **k):
            raise httpx.ConnectError("pas de workspace")

        async def aclose(self):
            pass

    monkeypatch.setattr(main.httpx, "AsyncClient", _Client)
    req = _Req({"content-type": "application/json",
                "x-workspace-token": "forge-par-le-client"})
    with pytest.raises(HTTPException):
        asyncio.run(main._proxy_request(req, "http://ws:8100/mcp", "s1"))
    assert vus.get("X-Workspace-Token") == JETON_REFERENCE
    assert "x-workspace-token" not in vus


def test_env_securite_workspace(monkeypatch):
    monkeypatch.delenv("WORKSPACE_AUTH_MODE", raising=False)
    env = sessions._env_securite_workspace()
    assert env == {"WORKSPACE_AUTH_MODE": "permissive", "STREAM_BIND_HOST": "127.0.0.1"}
    monkeypatch.setenv("WORKSPACE_AUTH_MODE", "ENFORCE")
    assert sessions._env_securite_workspace()["WORKSPACE_AUTH_MODE"] == "enforce"
    # Une faute de frappe ne doit pas ouvrir le workspace.
    monkeypatch.setenv("WORKSPACE_AUTH_MODE", "enforced")
    assert sessions._env_securite_workspace()["WORKSPACE_AUTH_MODE"] == "enforce"


def test_proxies_vnc_authentifies_dans_la_source():
    """Verrou de source : les deux relais noVNC posent l'authentification."""
    source = (_ROOT / "hub" / "main.py").read_text(encoding="utf-8")
    http = source.split("async def proxy_workspace_vnc_http")[1].split("\nasync def ")[0]
    ws = source.split("async def proxy_workspace_vnc_ws")[1].split("\nasync def ")[0]
    assert "workspace_auth.entete_basic_vnc()" in http
    assert "workspace_auth.entete_basic_vnc()" in ws


def test_tout_appel_direct_du_hub_au_mcp_du_workspace_porte_le_jeton():
    """Integration vague E + securite (2026-10-02) : les appels directs
    (`_mcp_url`) du suivi des taches et de l'execution d'etude envoyaient la
    cle de l'utilisateur, pas forcement HUB_API_KEY : refuses en `enforce`."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "hub" / "main.py").read_text(encoding="utf-8")
    for m in re.finditer(r"_mcp_url\(s\)", src):
        bloc = src[m.start():m.start() + 600]
        if "headers=" in bloc.split("\n\n")[0]:
            assert "workspace_auth.entetes_workspace()" in bloc, bloc[:200]
