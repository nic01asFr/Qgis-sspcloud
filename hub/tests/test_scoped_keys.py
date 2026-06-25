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

from hub import auth  # noqa: E402


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
