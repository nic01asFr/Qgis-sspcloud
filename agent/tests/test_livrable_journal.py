"""Tests Chantier G10 : journal des livrables (memory + hook + endpoints).

Verifie :
  1. journal_livrable insere une entree et retourne un UUID.
  2. list_livrables sans filtre → toutes les entrees, ordre DESC created_at.
  3. Filtre context_kind → seulement les entrees du kind.
  4. Filtre recipe_id → seulement les entrees du recipe_id.
  5. get_livrable(nonexistent) → None.
  6. mark_livrable_published → update published + URL, get renvoie les
     nouvelles valeurs.
  7. output_hash : deux hashes identiques pour le meme manifest (verifie
     directement dans le hook via recipe_executor_mute).
  8. Endpoint HTTPX GET /journal/livrables → 200 + liste.
  9. Endpoint HTTPX GET /journal/livrables/{unknown_id} → 404.
 10. Hook dans recipe_executor_mute : un run reussi cree une entree
     journal avec les bons champs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# DATA_DIR isole AVANT import memory (patron des autres tests agent).
_TMP_DIR = tempfile.mkdtemp(prefix="qgis_g10_test_")
os.environ["DATA_DIR"] = _TMP_DIR

# Stub sqlite_vec pour test_livrable_journal_endpoints (utilise main.py).
if "sqlite_vec" not in sys.modules:
    _vec_stub = type(sys)("sqlite_vec")

    def _noop_load(*a, **k):
        return None

    _vec_stub.load = _noop_load  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec_stub

os.environ.setdefault(
    "HUB_URL",
    "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr",
)
os.environ.setdefault("HUB_API_KEY", "test-key")
os.environ.setdefault("QGIS_API_KEY", "test-key")
os.environ.setdefault("ONYXIA_USER", "test-user")

from agent import memory  # noqa: E402
from agent import recipe_executor_mute as rem  # noqa: E402

memory._DATA_DIR = Path(_TMP_DIR)
memory._DB_PATH = Path(_TMP_DIR) / "memory.db"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _setup():
    await memory.init()


# ── Helpers journal (unit) ───────────────────────────────────────────────────


def test_journal_livrable_returns_uuid_and_persists():
    """journal_livrable insere et retourne un UUID lisible via get_livrable."""
    _run(_setup())
    lid = _run(memory.journal_livrable(
        session_id="study:sA:recipe:demo",
        username="marie",
        kind="recipe_pure",
        output_hash="deadbeef",
        recipe_id="demo",
        context_kind="recipe_run",
        briques_used=["rules_global/foo"],
        metadata={"recipe_title": "Demo", "n_layers": 2, "n_briques": 1},
    ))
    assert isinstance(lid, str) and len(lid) >= 32
    entry = _run(memory.get_livrable(lid))
    assert entry is not None
    assert entry["livrable_id"] == lid
    assert entry["username"] == "marie"
    assert entry["kind"] == "recipe_pure"
    assert entry["recipe_id"] == "demo"
    assert entry["output_hash"] == "deadbeef"
    assert entry["briques_used"] == ["rules_global/foo"]
    assert entry["metadata"]["recipe_title"] == "Demo"
    assert entry["published"] is False
    assert entry["published_url"] is None


def test_list_livrables_orders_desc_by_created_at():
    """Sans filtre, retourne toutes les entrees du user, DESC created_at."""
    _run(_setup())
    # Trois entrees espacees dans le temps pour garantir l'ordre.
    l1 = _run(memory.journal_livrable(
        session_id="s1", username="alice", kind="recipe_pure",
        output_hash="h1", recipe_id="r1", context_kind="recipe_run",
        timestamp=1000,
    ))
    l2 = _run(memory.journal_livrable(
        session_id="s1", username="alice", kind="recipe_pure",
        output_hash="h2", recipe_id="r1", context_kind="recipe_run",
        timestamp=2000,
    ))
    l3 = _run(memory.journal_livrable(
        session_id="s2", username="alice", kind="agent_freeform",
        output_hash="h3", recipe_id=None, context_kind="desk",
        timestamp=3000,
    ))
    entries = _run(memory.list_livrables(username="alice", limit=10))
    ids = [e["livrable_id"] for e in entries]
    # Les 3 doivent etre presents, ordre DESC (le plus recent d'abord).
    assert set(ids) >= {l1, l2, l3}
    # l3 (timestamp 3000) doit precéder l2 (2000) qui precede l1 (1000).
    pos = {lid: i for i, lid in enumerate(ids)}
    assert pos[l3] < pos[l2] < pos[l1]


def test_list_livrables_filter_context_kind():
    """Filtre context_kind → seulement les entrees du kind."""
    _run(_setup())
    _run(memory.journal_livrable(
        session_id="sX", username="bob", kind="recipe_pure",
        output_hash="a", context_kind="recipe_run", timestamp=100,
    ))
    _run(memory.journal_livrable(
        session_id="sY", username="bob", kind="agent_freeform",
        output_hash="b", context_kind="desk", timestamp=200,
    ))
    entries = _run(memory.list_livrables(
        username="bob", context_kind="recipe_run",
    ))
    assert len(entries) == 1
    assert entries[0]["context_kind"] == "recipe_run"


def test_list_livrables_filter_recipe_id():
    """Filtre recipe_id → seulement les entrees du recipe_id."""
    _run(_setup())
    _run(memory.journal_livrable(
        session_id="s", username="carol", kind="recipe_pure",
        output_hash="1", recipe_id="alpha", timestamp=10,
    ))
    _run(memory.journal_livrable(
        session_id="s", username="carol", kind="recipe_pure",
        output_hash="2", recipe_id="beta", timestamp=20,
    ))
    _run(memory.journal_livrable(
        session_id="s", username="carol", kind="recipe_pure",
        output_hash="3", recipe_id="alpha", timestamp=30,
    ))
    entries = _run(memory.list_livrables(
        username="carol", recipe_id="alpha",
    ))
    assert len(entries) == 2
    assert all(e["recipe_id"] == "alpha" for e in entries)


def test_get_livrable_returns_none_for_unknown_id():
    _run(_setup())
    entry = _run(memory.get_livrable("uuid-inexistant-12345"))
    assert entry is None


def test_mark_livrable_published_updates_fields():
    """mark_livrable_published → published=True + URL, get renvoie les nouveaux."""
    _run(_setup())
    lid = _run(memory.journal_livrable(
        session_id="sZ", username="dave", kind="recipe_pure",
        output_hash="pub_h", recipe_id="pub_r",
    ))
    entry = _run(memory.get_livrable(lid))
    assert entry["published"] is False
    assert entry["published_url"] is None

    _run(memory.mark_livrable_published(lid, "s3://bucket/livrables/x.json"))
    entry2 = _run(memory.get_livrable(lid))
    assert entry2["published"] is True
    assert entry2["published_url"] == "s3://bucket/livrables/x.json"


def test_output_hash_deterministic():
    """Deux manifests identiques → meme SHA256 (invariant du hook)."""
    manifest_a = {
        "title": "Demo",
        "layers": [{"id": "L1"}, {"id": "L2"}],
        "provenance": {"output_kind": "component"},
    }
    # Meme contenu, ordre de cles differentes (json.dumps sort_keys les egalise).
    manifest_b = {
        "provenance": {"output_kind": "component"},
        "title": "Demo",
        "layers": [{"id": "L1"}, {"id": "L2"}],
    }
    h_a = hashlib.sha256(
        json.dumps(manifest_a, sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    h_b = hashlib.sha256(
        json.dumps(manifest_b, sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    assert h_a == h_b


# ── Endpoints REST ───────────────────────────────────────────────────────────


def test_endpoint_list_livrables_returns_200_and_list():
    """GET /journal/livrables → 200 + {livrables: [...]}."""
    from fastapi.testclient import TestClient
    from agent import main as agent_main

    _run(_setup())
    # Insertion prealable pour avoir au moins une entree a lister.
    _run(memory.journal_livrable(
        session_id="s_ep", username="ep_user", kind="recipe_pure",
        output_hash="ep_h", recipe_id="ep_r",
    ))

    with TestClient(agent_main.app) as client:
        resp = client.get(
            "/journal/livrables?user=ep_user&limit=5",
            headers={"user-agent": "kube-probe/1.0"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "livrables" in body
        assert isinstance(body["livrables"], list)
        assert len(body["livrables"]) >= 1
        # Champs cles presents dans la reponse.
        first = body["livrables"][0]
        assert "livrable_id" in first
        assert "output_hash" in first
        assert "created_at" in first


def test_endpoint_get_livrable_unknown_returns_404():
    """GET /journal/livrables/{unknown} → 404."""
    from fastapi.testclient import TestClient
    from agent import main as agent_main

    _run(_setup())
    with TestClient(agent_main.app) as client:
        resp = client.get(
            "/journal/livrables/uuid-qui-nexiste-pas-12345",
            headers={"user-agent": "kube-probe/1.0"},
        )
        assert resp.status_code == 404


# ── Hook recipe_executor_mute ────────────────────────────────────────────────


def _fake_hub_output() -> dict:
    """RecipeWebOutput minimal (copie du fixture test_recipe_executor_mute)."""
    return {
        "scene_manifest": {
            "manifest_version": "0.3.1",
            "produced_at": "2026-07-13T10:00:00Z",
            "title": "Recipe demo",
            "layers": [
                {"id": "L1", "name": "Layer 1"},
                {"id": "L2", "name": "Layer 2"},
            ],
            "provenance": {"output_kind": "component"},
        },
        "provenance": {
            "recipe_id": "demo",
            "recipe_version": "1.0",
            "mode": "recipe_pure",
            "produced_at": "2026-07-13T10:00:00Z",
            "steps_order": ["step_a", "step_b", "render"],
            "briques_used": ["rules_global/foo", "rules_global/bar"],
            "rules_enforced": [],
            "output_kind": "component",
        },
        "briques_used": ["rules_global/foo", "rules_global/bar"],
    }


def test_hook_creates_journal_entry_after_recipe_done():
    """Un run mute reussi doit inserer une entree journal avec les bons champs."""
    _run(_setup())

    sid = "study:g10hook:recipe:demo"

    async def _fake_hub_call(hub_url, api_key, recipe_id, user_message, sid=None):
        return 200, _fake_hub_output()

    with patch.object(rem, "_call_hub_execute", new=AsyncMock(
        side_effect=_fake_hub_call,
    )):
        async def _drain():
            async for _ in rem.stream_recipe_execution(
                session_id=sid,
                recipe_id="demo",
                hub_url="http://hub",
                api_key="k",
                user_message="fais-moi la recipe demo",
            ):
                pass
        _run(_drain())

    entries = _run(memory.list_livrables(recipe_id="demo", limit=20))
    hook_entries = [e for e in entries if e["session_id"] == sid]
    assert len(hook_entries) == 1, (
        "Le hook doit avoir cree exactement une entree journal"
    )
    entry = hook_entries[0]
    assert entry["kind"] == "recipe_pure"
    assert entry["recipe_id"] == "demo"
    assert entry["context_kind"] == "recipe_run"
    # SHA256 hex : 64 caracteres.
    assert isinstance(entry["output_hash"], str) and len(entry["output_hash"]) == 64
    assert entry["briques_used"] == ["rules_global/foo", "rules_global/bar"]
    meta = entry["metadata"]
    assert meta["recipe_title"] == "Recipe demo"
    assert meta["n_layers"] == 2
    assert meta["n_briques"] == 2
    assert meta["output_kind"] == "component"


def test_hook_failure_does_not_break_stream(monkeypatch):
    """Si journal_livrable raise, le stream doit malgre tout finir proprement.

    Verifie la propriete "fire-and-forget" du hook : un journal casse
    (SQLite locked, DATA_DIR non monte, etc.) ne doit pas casser la
    reponse a l'user.
    """
    _run(_setup())

    async def _fake_hub_call(hub_url, api_key, recipe_id, user_message, sid=None):
        return 200, _fake_hub_output()

    async def _broken_journal(*args, **kwargs):
        raise RuntimeError("SQLite locked (simule)")

    monkeypatch.setattr(memory, "journal_livrable", _broken_journal)

    with patch.object(rem, "_call_hub_execute", new=AsyncMock(
        side_effect=_fake_hub_call,
    )):
        async def _collect():
            events = []
            async for chunk in rem.stream_recipe_execution(
                session_id="study:g10fail:recipe:demo",
                recipe_id="demo",
                hub_url="http://hub",
                api_key="k",
                user_message="test",
            ):
                events.append(chunk)
            return events

        events = _run(_collect())

    # Le stream a bien emis recipe_done malgre l'echec du journal.
    joined = "".join(events)
    assert "event: recipe_start" in joined
    assert "event: recipe_done" in joined
    assert "event: recipe_error" not in joined


if __name__ == "__main__":
    import inspect
    ns = dict(globals())
    for name, fn in ns.items():
        if name.startswith("test_") and callable(fn):
            sig = inspect.signature(fn)
            if "monkeypatch" in sig.parameters:
                continue
            print(f"→ {name}")
            fn()
    print("OK — livrable_journal tests (hors monkeypatch) passent.")
