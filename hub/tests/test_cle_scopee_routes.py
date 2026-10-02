"""Une cle scopee n'est recevable que la ou son scope est applique (SEC-1).

Constate le 2026-09-26 : le middleware laissait passer une cle `qgisk_` sur
tous les prefixes inter-pod (`/studies`, `/publish`, `/admin`, `/internal`...)
et `get_current_user` rendait l'identite complete du proprietaire, avec le role
admin s'il l'avait. Seul `/mcp` filtrait les outils. Ces tests verrouillent le
refus explicite ailleurs, et le maintien des deux usages legitimes : le proxy
`/mcp` et la lecture des livrables publies.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.security import HTTPAuthorizationCredentials  # noqa: E402

from hub import auth  # noqa: E402


class _URL:
    def __init__(self, path: str):
        self.path = path


class _Req:
    def __init__(self, path, headers=None, cookies=None):
        self.url = _URL(path)
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.state = type("S", (), {})()
        self.base_url = "https://exemple.test/"


_PASSE = object()


async def _suite(_req):
    return _PASSE


def _base(tmp_path, monkeypatch):
    auth._DATA_DIR = tmp_path
    auth._DB_PATH = tmp_path / "apikeys.db"
    auth._cached_key = {"value": "", "ts": 0.0}
    asyncio.run(auth.init_apikeys_db())
    # La cle superviseur n'est pas en jeu : aucun Bearer presente ici ne
    # doit passer par `_is_inter_pod_authorized`.
    async def _jamais(_req):
        return False
    monkeypatch.setattr(auth, "_is_inter_pod_authorized", _jamais)


def _cle(**kw):
    return asyncio.run(auth.create_scoped_key(
        "alice", "d8a0b9718857", tools=["get_screenshot"], **kw))


@pytest.mark.parametrize("chemin", [
    "/studies/d8a0b9718857/scoped-keys",
    "/studies/active",
    "/publish/storymap/demo",
    "/admin/agent-config",
    "/api/admin/restart",
    "/internal/profiles/standard/full",
    "/agent-context/new",
    "/diagnostics/isolation",
    "/sessions",
    "/api/recipes-web/execute",
])
def test_middleware_refuse_cle_scopee_hors_mcp(tmp_path, monkeypatch, chemin):
    _base(tmp_path, monkeypatch)
    cle = _cle()
    req = _Req(chemin, headers={"authorization": f"Bearer {cle}"})
    res = asyncio.run(auth.oidc_auth_middleware(req, _suite))
    assert res is not _PASSE
    assert res.status_code == 403
    assert b"MCP" in bytes(res.body)
    assert getattr(req.state, "scope", None) is None


def test_middleware_accepte_cle_scopee_sur_mcp(tmp_path, monkeypatch):
    _base(tmp_path, monkeypatch)
    cle = _cle()
    for chemin in ("/mcp", "/mcp/messages"):
        req = _Req(chemin, headers={"authorization": f"Bearer {cle}"})
        assert asyncio.run(auth.oidc_auth_middleware(req, _suite)) is _PASSE
        assert req.state.scope["tools"] == ["get_screenshot"]


def test_jeton_oauth_meme_regle(tmp_path, monkeypatch):
    """Un jeton OAuth est une cle scopee `supervisor` : /mcp seulement."""
    _base(tmp_path, monkeypatch)
    jeton, _ = asyncio.run(auth.create_oauth_token("alice", "client-x"))
    ok = _Req("/mcp", headers={"authorization": f"Bearer {jeton}"})
    assert asyncio.run(auth.oidc_auth_middleware(ok, _suite)) is _PASSE
    ko = _Req("/studies/active", headers={"authorization": f"Bearer {jeton}"})
    assert asyncio.run(auth.oidc_auth_middleware(ko, _suite)).status_code == 403


def test_get_current_user_refuse_hors_routes(tmp_path, monkeypatch):
    _base(tmp_path, monkeypatch)
    cle = _cle()
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=cle)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.get_current_user(
            request=_Req("/studies/active"), creds=creds))
    assert exc.value.status_code == 403


def test_get_current_user_accepte_mcp_et_publies(tmp_path, monkeypatch):
    _base(tmp_path, monkeypatch)
    cle = _cle()
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=cle)
    for chemin in ("/mcp", "/published/storymap/x", "/p/abc"):
        user = asyncio.run(auth.get_current_user(request=_Req(chemin), creds=creds))
        assert user["source"] == "scoped"
        assert user["scope"]["tools"] == ["get_screenshot"]


def test_cle_scopee_jamais_admin(tmp_path, monkeypatch):
    _base(tmp_path, monkeypatch)
    monkeypatch.setattr(auth, "_ADMIN_USERS", {"alice"})
    cle = _cle()
    user = asyncio.run(auth._validate_scoped_key(cle))
    assert user["role"] == "user"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.require_admin(user))
    assert exc.value.status_code == 403
    # Le proprietaire, lui, garde son role via sa cle personnelle.
    assert asyncio.run(auth.require_admin(
        {"username": "alice", "role": "admin", "source": "api_key"}))


def test_prefixes_autorises_sans_debordement():
    assert auth.scoped_key_path_allowed("/mcp")
    assert auth.scoped_key_path_allowed("/p/x")
    # `/publish` ne doit pas etre pris pour `/p` ou `/published`.
    assert not auth.scoped_key_path_allowed("/publish/storymap/x")
    assert not auth.scoped_key_path_allowed("/mcpx")
    assert not auth.scoped_key_path_allowed("/publishedx")
