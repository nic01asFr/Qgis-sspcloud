"""Tests couche donnees des cles scopees (auth.scoped_keys) — étape 1 additive.

Verrouille : create/validate/revoke/list, scope resolu correctement, expiration,
prefixe-guard (une cle superviseur `qgis_` n'est pas une cle scopee), absence de
UNIQUE(study,project) (plusieurs agents publies sur le meme projet), et non-
exposition de la cle brute dans le listing.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from fastapi.security import HTTPAuthorizationCredentials  # noqa: E402

from hub import auth  # noqa: E402


class _FakeURL:
    def __init__(self, path: str):
        self.path = path


class _FakeReq:
    """Request minimal pour exercer le middleware sur le chemin scope."""

    def __init__(self, path, headers=None, cookies=None):
        self.url = _FakeURL(path)
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.state = type("S", (), {})()
        # Le middleware construit l'URL de connexion depuis `base_url`.
        # Ce double l'ignorait : les tests echouaient sur un
        # AttributeError, pas sur ce qu'ils verifiaient.
        self.base_url = "https://exemple.test/"


_SENT = object()


async def _call_next(_req):
    return _SENT


def _setup(tmp_path):
    """Isole la DB sur un tmp_path + reset le cache cle superviseur."""
    auth._DATA_DIR = tmp_path
    auth._DB_PATH = tmp_path / "apikeys.db"
    auth._cached_key = {"value": "", "ts": 0.0}
    asyncio.run(auth.init_apikeys_db())


def test_create_and_validate(tmp_path):
    _setup(tmp_path)
    key = asyncio.run(auth.create_scoped_key(
        "alice", "d8a0b9718857", project_id="0780d1d825f3",
        persona="storymap_creator", tools=["run_recipe", "list_layers"],
        data_scope="project", mode="scoped", actor="delegate",
        label="Agent Storymap"))
    assert key.startswith("qgisk_alice_")
    v = asyncio.run(auth._validate_scoped_key(key))
    assert v is not None
    assert v["username"] == "alice"
    assert v["source"] == "scoped"
    s = v["scope"]
    assert s == {
        "owner": "alice", "sid": "d8a0b9718857", "pid": "0780d1d825f3",
        "persona": "storymap_creator", "tools": ["run_recipe", "list_layers"],
        "data": "project", "mode": "scoped", "actor": "delegate",
    }


def test_tools_all_default_and_study_wide(tmp_path):
    _setup(tmp_path)
    key = asyncio.run(auth.create_scoped_key("bob", "aaaaaaaaaaaa"))
    v = asyncio.run(auth._validate_scoped_key(key))
    assert v["scope"]["tools"] == "all"
    assert v["scope"]["pid"] is None      # project_id NULL = etude entiere
    assert v["scope"]["data"] == "project"
    assert v["scope"]["mode"] == "scoped"


def test_revoke(tmp_path):
    _setup(tmp_path)
    key = asyncio.run(auth.create_scoped_key("alice", "d8a0b9718857"))
    assert asyncio.run(auth._validate_scoped_key(key)) is not None
    asyncio.run(auth.revoke_scoped_key(key))
    assert asyncio.run(auth._validate_scoped_key(key)) is None


def test_expired(tmp_path):
    _setup(tmp_path)
    key = asyncio.run(auth.create_scoped_key(
        "alice", "d8a0b9718857", expires_at=1))  # 1970 -> expire
    assert asyncio.run(auth._validate_scoped_key(key)) is None


def test_unknown_and_prefix_guard(tmp_path):
    _setup(tmp_path)
    # cle inconnue (bon prefixe mais absente)
    assert asyncio.run(auth._validate_scoped_key("qgisk_alice_deadbeef")) is None
    # cle SUPERVISEUR (prefixe qgis_) n'est PAS une cle scopee
    assert asyncio.run(auth._validate_scoped_key("qgis_alice_xxx")) is None
    assert asyncio.run(auth._validate_scoped_key("")) is None
    assert asyncio.run(auth._validate_scoped_key(None)) is None


def test_list_excludes_revoked_and_masks_key(tmp_path):
    _setup(tmp_path)
    asyncio.run(auth.create_scoped_key("alice", "d8a0b9718857", label="A"))
    k2 = asyncio.run(auth.create_scoped_key("alice", "ffffffffffff", label="B"))
    asyncio.run(auth.revoke_scoped_key(k2))
    lst = asyncio.run(auth.list_scoped_keys("alice"))
    assert len(lst) == 1
    assert lst[0]["label"] == "A"
    assert "id" not in lst[0]                 # cle brute jamais exposee
    assert lst[0]["id_masked"].endswith("…")
    full = asyncio.run(auth.list_scoped_keys("alice", include_revoked=True))
    assert len(full) == 2


def test_multiple_agents_same_project(tmp_path):
    """Pas de UNIQUE(username,study,project) : 2 agents distincts, meme projet."""
    _setup(tmp_path)
    k1 = asyncio.run(auth.create_scoped_key(
        "alice", "d8a0b9718857", project_id="p1aaaaaaaaaa", persona="a"))
    k2 = asyncio.run(auth.create_scoped_key(
        "alice", "d8a0b9718857", project_id="p1aaaaaaaaaa", persona="b"))
    assert k1 != k2
    assert asyncio.run(auth._validate_scoped_key(k1))["scope"]["persona"] == "a"
    assert asyncio.run(auth._validate_scoped_key(k2))["scope"]["persona"] == "b"


# ── Etape 2 : resolution auth (get_current_user + middleware) ──────────────────

def test_bearer_scope_helper(tmp_path):
    _setup(tmp_path)
    key = asyncio.run(auth.create_scoped_key("alice", "d8a0b9718857"))
    req = _FakeReq("/mcp", headers={"authorization": f"Bearer {key}"})
    scope = asyncio.run(auth._bearer_scope(req))
    assert scope and scope["sid"] == "d8a0b9718857"
    # bearer non-scope (cle superviseur) -> None
    req2 = _FakeReq("/mcp", headers={"authorization": "Bearer qgis_alice_xxx"})
    assert asyncio.run(auth._bearer_scope(req2)) is None
    # pas de header -> None
    assert asyncio.run(auth._bearer_scope(_FakeReq("/mcp"))) is None


def test_get_current_user_scoped_key(tmp_path):
    _setup(tmp_path)
    key = asyncio.run(auth.create_scoped_key("bob", "aaaaaaaaaaaa", persona="x"))
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=key)
    user = asyncio.run(auth.get_current_user(request=None, creds=creds))
    assert user["username"] == "bob"
    assert user["source"] == "scoped"
    assert user["scope"]["persona"] == "x"


def test_get_current_user_invalid_scoped_key_401(tmp_path):
    _setup(tmp_path)
    creds = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="qgisk_bob_deadbeefdeadbeef")
    with pytest.raises(Exception):  # HTTPException 401
        asyncio.run(auth.get_current_user(request=None, creds=creds))


def test_middleware_accepts_scoped_key_on_mcp(tmp_path):
    _setup(tmp_path)
    key = asyncio.run(auth.create_scoped_key(
        "alice", "d8a0b9718857", project_id="0780d1d825f3", tools=["run_recipe"]))
    req = _FakeReq("/mcp", headers={"authorization": f"Bearer {key}"})
    res = asyncio.run(auth.oidc_auth_middleware(req, _call_next))
    assert res is _SENT                              # passthrough autorise
    assert getattr(req.state, "scope", None) is not None
    assert req.state.scope["sid"] == "d8a0b9718857"
    assert req.state.scope["pid"] == "0780d1d825f3"
    assert req.state.scope["tools"] == ["run_recipe"]


def test_middleware_rejects_invalid_scoped_key_on_mcp(tmp_path):
    _setup(tmp_path)
    req = _FakeReq("/mcp", headers={"authorization": "Bearer qgisk_x_unknown"})
    res = asyncio.run(auth.oidc_auth_middleware(req, _call_next))
    assert res is not _SENT                          # pas de passthrough
    assert getattr(res, "status_code", None) == 401
