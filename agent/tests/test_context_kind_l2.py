"""Tests Chantier G9 : composition L2 par context_kind + hub_scope_client.

Verifie :
  - build_context_summary backward-compat : sans context_kind = comportement
    legacy identique a l'ancien code.
  - context_kind="desk" == None (alias legacy).
  - Chaque kind cible produit une section markdown attendue avec les IDs.
  - editor_freeform → bloc court.
  - hub_scope_client : fail-soft si hub down, cache TTL 30s bloque le 2eme
    appel HTTP dans la fenetre.
  - Regression : les tests G1 (session_tags) restent verts (on ne touche pas
    aux tables ni aux signatures existantes).
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Path setup : agent/agent/ doit etre importable
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Forcer DATA_DIR temporaire AVANT l'import de memory.py.
_TMP_DIR = tempfile.mkdtemp(prefix="qgis_g9_test_")
os.environ["DATA_DIR"] = _TMP_DIR

from agent import memory  # noqa: E402
from agent import hub_scope_client  # noqa: E402

# Forcer DB_PATH sur notre tempdir (le module a pu etre importe avec un
# DATA_DIR precedent par un autre test dans la meme session).
memory._DATA_DIR = Path(_TMP_DIR)
memory._DB_PATH = Path(_TMP_DIR) / "memory_g9.db"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _setup():
    await memory.init()


# ── Fixtures ────────────────────────────────────────────────────────────────

_ACTIVE_STUDY = {
    "sid": "sid42",
    "name": "Etude Marie Blancarde",
    "profile": "diagnostic_temporel",
}
_STUDY_ARTIFACTS = {
    "components": {"total": 3, "by_kind": {"carte": 2, "chart": 1}, "recent": []},
    "assemblies": {"total": 1, "by_status": {"draft": 1}, "recent": []},
}


# ── Backward-compat : context_kind absent = comportement legacy ─────────────

def test_backward_compat_no_context_kind():
    """build_context_summary sans nouveaux params → format legacy."""
    _run(_setup())
    hub_scope_client.reset_cache()

    out = _run(memory.build_context_summary(
        username="user",
        session_id="study:sid42",
        active_study=_ACTIVE_STUDY,
        study_artifacts=_STUDY_ARTIFACTS,
    ))
    # Le titre legacy est present, aucune section scope-aware.
    assert "=== Étude en cours ===" in out
    assert "Étude active : « Etude Marie Blancarde »" in out
    assert "=== Composant en édition ===" not in out
    assert "=== Assembly en édition ===" not in out
    assert "=== Recipe en cours d'exécution ===" not in out


def test_context_kind_desk_alias_legacy():
    """context_kind='desk' doit produire exactement la meme sortie que None."""
    _run(_setup())
    hub_scope_client.reset_cache()

    out_none = _run(memory.build_context_summary(
        username="user",
        session_id="study:sid42",
        active_study=_ACTIVE_STUDY,
        study_artifacts=_STUDY_ARTIFACTS,
    ))
    hub_scope_client.reset_cache()
    out_desk = _run(memory.build_context_summary(
        username="user",
        session_id="study:sid42",
        active_study=_ACTIVE_STUDY,
        study_artifacts=_STUDY_ARTIFACTS,
        context_kind="desk",
        scope_ids={"sid": "sid42"},
    ))
    assert out_none == out_desk


# ── Kinds cibles : la section attendue apparait ────────────────────────────

def test_assist_component_focus_on_cid():
    """context_kind='assist_component' + cid → bloc "Composant actif : X"."""
    _run(_setup())
    hub_scope_client.reset_cache()

    out = _run(memory.build_context_summary(
        username="user",
        session_id="assist:sid42:cid:comp-xyz",
        active_study=_ACTIVE_STUDY,
        context_kind="assist_component",
        scope_ids={"sid": "sid42", "cid": "comp-xyz"},
        # Pas de hub_url/hub_key → fetch fail-soft, la section reste creee.
    ))
    assert "=== Composant en édition ===" in out
    assert "Composant actif : comp-xyz" in out
    # Le rappel etude bref doit apparaitre puisque active_study est fourni.
    assert "=== Rappel étude (bref) ===" in out
    assert "Étude parente : « Etude Marie Blancarde »" in out


def test_assist_assembly_focus_on_aid():
    """context_kind='assist_assembly' + aid → bloc "Assembly actif : Y"."""
    _run(_setup())
    hub_scope_client.reset_cache()

    out = _run(memory.build_context_summary(
        username="user",
        session_id="assist:sid42:aid:asm-abc",
        active_study=_ACTIVE_STUDY,
        context_kind="assist_assembly",
        scope_ids={"sid": "sid42", "aid": "asm-abc"},
    ))
    assert "=== Assembly en édition ===" in out
    assert "Assembly actif : asm-abc" in out


def test_recipe_run_focus_on_recipe_id():
    """context_kind='recipe_run' + recipe_id → bloc "Recipe en exécution : Z"."""
    _run(_setup())
    hub_scope_client.reset_cache()

    out = _run(memory.build_context_summary(
        username="user",
        session_id="study:sid42:recipe:r_choropleth",
        active_study=_ACTIVE_STUDY,
        context_kind="recipe_run",
        scope_ids={"sid": "sid42", "recipe_id": "r_choropleth"},
    ))
    assert "=== Recipe en cours d'exécution ===" in out
    assert "Recipe en exécution : r_choropleth" in out


def test_editor_freeform_minimal():
    """context_kind='editor_freeform' → bloc court, contient Draft W."""
    _run(_setup())
    hub_scope_client.reset_cache()

    out = _run(memory.build_context_summary(
        username="user",
        session_id="study:sid42:draft:draft-w",
        active_study=_ACTIVE_STUDY,
        context_kind="editor_freeform",
        scope_ids={"sid": "sid42", "draft_id": "draft-w"},
    ))
    assert "=== Éditeur freeform ===" in out
    assert "Draft draft-w en cours" in out
    # Section "Composant/Assembly" ne doit PAS apparaitre.
    assert "=== Composant en édition ===" not in out
    assert "=== Assembly en édition ===" not in out


def test_unknown_context_kind_falls_back_to_desk():
    """Un kind inconnu doit se replier sur le comportement legacy."""
    _run(_setup())
    hub_scope_client.reset_cache()

    out = _run(memory.build_context_summary(
        username="user",
        session_id="study:sid42",
        active_study=_ACTIVE_STUDY,
        study_artifacts=_STUDY_ARTIFACTS,
        context_kind="mode_hypothetique_qui_nexiste_pas",
        scope_ids={"sid": "sid42"},
    ))
    # Meme rendu que None (repli desk).
    assert "=== Étude en cours ===" in out
    assert "=== Composant en édition ===" not in out


# ── hub_scope_client : fail-soft + cache ─────────────────────────────────────

def test_fetch_component_history_hub_down_returns_empty():
    """Hub inatteignable → [] sans exception."""
    hub_scope_client.reset_cache()
    # URL manifestement inatteignable : port 1 non-ecouté.
    got = _run(hub_scope_client.fetch_component_history(
        hub_url="http://127.0.0.1:1", api_key="dummy",
        sid="sid42", cid="comp-xyz",
    ))
    assert got == []


def test_fetch_recipe_recent_runs_uses_memory_tag():
    """fetch_recipe_recent_runs lit session_tags via find_sessions_by_tag."""
    _run(_setup())
    hub_scope_client.reset_cache()
    # Setup : 2 sessions taggees recipe_run_ok=r_test
    _run(memory.set_session_tag("sess-A", "recipe_run_ok", "r_test"))
    _run(memory.set_session_tag("sess-B", "recipe_run_ok", "r_test"))

    got = _run(hub_scope_client.fetch_recipe_recent_runs(
        hub_url="", api_key="", recipe_id="r_test", limit=3,
    ))
    session_ids = {r["session_id"] for r in got}
    assert session_ids == {"sess-A", "sess-B"}


def test_cache_ttl_blocks_second_http_call():
    """Deux appels consecutifs dans la fenetre TTL → 1 seul HTTP call."""
    hub_scope_client.reset_cache()

    call_counter = {"n": 0}

    class _MockResp:
        status_code = 200

        def json(self):
            return [{"version": 1, "summary": "creation"}]

    class _MockClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            call_counter["n"] += 1
            return _MockResp()

    with patch("agent.hub_scope_client.httpx.AsyncClient", _MockClient):
        got1 = _run(hub_scope_client.fetch_component_history(
            hub_url="http://hub.local", api_key="k",
            sid="s1", cid="c1",
        ))
        got2 = _run(hub_scope_client.fetch_component_history(
            hub_url="http://hub.local", api_key="k",
            sid="s1", cid="c1",
        ))
    assert got1 == got2
    assert len(got1) == 1
    # 1er appel HTTP, 2eme sert du cache → 1 appel HTTP total.
    assert call_counter["n"] == 1


def test_assist_component_uses_hub_history_when_available():
    """Quand hub renvoie un historique, les versions apparaissent dans le L2."""
    _run(_setup())
    hub_scope_client.reset_cache()

    class _MockResp:
        status_code = 200

        def json(self):
            return [
                {"version": 3, "author": "marie", "summary": "ajout legende"},
                {"version": 2, "author": "marie", "summary": "creation"},
            ]

    class _MockClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return _MockResp()

    with patch("agent.hub_scope_client.httpx.AsyncClient", _MockClient):
        out = _run(memory.build_context_summary(
            username="user",
            session_id="assist:sid42:cid:comp-xyz",
            active_study=_ACTIVE_STUDY,
            context_kind="assist_component",
            scope_ids={"sid": "sid42", "cid": "comp-xyz"},
            hub_url="http://hub.local",
            hub_key="apikey",
        ))
    assert "Historique éditorial (2 versions récentes)" in out
    assert "ajout legende" in out
    assert "marie" in out


def test_recipe_create_bloc_present():
    """context_kind='recipe_create' → bloc "Recipe en édition : X"."""
    _run(_setup())
    hub_scope_client.reset_cache()

    out = _run(memory.build_context_summary(
        username="user",
        session_id="study:sid42:recipe_edit:my-recipe",
        active_study=_ACTIVE_STUDY,
        context_kind="recipe_create",
        scope_ids={"sid": "sid42", "recipe_id": "my-recipe"},
    ))
    assert "=== Recipe en édition ===" in out
    assert "Recipe en édition : my-recipe" in out


# ── Scope_ids partiel : robustesse ──────────────────────────────────────────

def test_scope_ids_partial_uses_placeholder():
    """context_kind cible sans scope_ids → placeholder '?', pas de crash."""
    _run(_setup())
    hub_scope_client.reset_cache()

    out = _run(memory.build_context_summary(
        username="user",
        session_id="assist:sid42:cid:missing",
        active_study=_ACTIVE_STUDY,
        context_kind="assist_component",
        scope_ids={},  # PAS de cid
    ))
    assert "=== Composant en édition ===" in out
    assert "Composant actif : ?" in out
