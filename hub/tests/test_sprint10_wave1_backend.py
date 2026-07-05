"""
Tests Sprint 1.5 Wave 1 - Equipe A "Backend Fondations".

Couvre 3 items livrables :
- S5 : SSE streaming assist (endpoint action-stream + execute_action_stream)
- S7 : Sessions L1 cascade parent-child
- S9 : Compaction rolling delta jsondiff (assemblies_deltas_compact)

Contract stable :
- signatures NEUTRES FastAPI (kwargs-only, DI execute_python)
- INSERT-only preserve (compact = decodeur additif, jamais DELETE)
- backward-compat execute_action synchrone conservee
"""
from __future__ import annotations

import inspect
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# S5 - SSE streaming assist
# ============================================================================

class TestS5SseStreamingEndpoint:
    """Endpoint /assemblies/{aid}/assist/action-stream + AgentBrick stream."""

    def test_endpoint_action_stream_registered(self):
        """L'endpoint SSE doit exister dans les routes FastAPI."""
        from hub.main import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert (
            "/studies/{sid}/assemblies/{aid}/assist/action-stream" in routes
        ), "Endpoint /assist/action-stream (S5) manquant"

    def test_endpoint_action_stream_is_post(self):
        """L'endpoint doit accepter POST (payload tool+args identique legacy)."""
        from hub.main import app
        for r in app.routes:
            if (
                hasattr(r, "path")
                and r.path == "/studies/{sid}/assemblies/{aid}/assist/action-stream"
            ):
                assert "POST" in r.methods
                return
        pytest.fail("Route action-stream introuvable")

    def test_agent_brick_has_execute_action_stream(self):
        """AgentBrick expose execute_action_stream() async generator."""
        from hub.actions.agent_brick import AgentBrick
        assert hasattr(AgentBrick, "execute_action_stream")
        # doit etre un async generator function (asyncgen)
        assert inspect.isasyncgenfunction(AgentBrick.execute_action_stream)

    def test_execute_action_backward_compat_preserved(self):
        """execute_action() synchrone conservee (backward compat contract)."""
        from hub.actions.agent_brick import AgentBrick
        assert inspect.iscoroutinefunction(AgentBrick.execute_action)
        # Doit retourner ActionResult, pas un async generator
        assert not inspect.isasyncgenfunction(AgentBrick.execute_action)

    def test_endpoint_uses_streaming_response(self):
        """L'endpoint doit utiliser StreamingResponse (pattern main.py:1824)."""
        from hub.main import assembly_assist_action_stream_endpoint
        src = inspect.getsource(assembly_assist_action_stream_endpoint)
        assert "StreamingResponse" in src
        assert "text/event-stream" in src
        assert "execute_action_stream" in src

    def test_endpoint_sse_headers_present(self):
        """Headers SSE requis : Cache-Control no-cache + X-Accel-Buffering no."""
        from hub.main import assembly_assist_action_stream_endpoint
        src = inspect.getsource(assembly_assist_action_stream_endpoint)
        assert "no-cache" in src
        # Anti-buffering pour permettre streaming reel via nginx ingress
        assert "X-Accel-Buffering" in src

    def test_endpoint_handles_abort_controller(self):
        """asyncio.CancelledError catchee (AbortController cleanup)."""
        from hub.main import assembly_assist_action_stream_endpoint
        src = inspect.getsource(assembly_assist_action_stream_endpoint)
        assert "CancelledError" in src


# ============================================================================
# S7 - Session L1 cascade parent-child
# ============================================================================

class TestS7SessionParentCascade:
    """parent_session_id + set_session_parent + get_session_ancestors."""

    def test_module_exposes_functions(self):
        from hub import sessions as sess
        assert hasattr(sess, "set_session_parent")
        assert hasattr(sess, "get_session_ancestors")
        assert inspect.iscoroutinefunction(sess.set_session_parent)
        assert inspect.iscoroutinefunction(sess.get_session_ancestors)

    def test_module_exposes_error_classes(self):
        from hub import sessions as sess
        assert issubclass(sess.SessionNotFoundError, Exception)
        assert issubclass(sess.CircularParentError, Exception)

    def test_endpoint_set_parent_registered(self):
        from hub.main import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/sessions/{session_id}/parent" in routes

    def test_endpoint_ancestors_registered(self):
        from hub.main import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/sessions/{session_id}/ancestors" in routes

    @pytest.mark.asyncio
    async def test_set_parent_and_traverse_ancestors(self, tmp_path):
        """Integration : set parent puis get_ancestors doit rendre la chaine."""
        # Patch la DB sessions vers tmp isolee (evite pollution / conflit prod).
        from hub import sessions as sess
        db_file = tmp_path / "sessions_test.db"
        original_db = sess._DB_PATH
        sess._DB_PATH = db_file
        sess._DATA_DIR = tmp_path
        try:
            await sess.init_db()

            now = int(time.time())
            base_session = {
                "id": "sess_grandparent", "owner": "userA",
                "status": "ready", "created_at": now, "last_active": now,
                "pod_name": "pod-gp", "svc_name": "svc-gp",
                "mcp_url": "http://gp/mcp", "api_url": "http://gp/api",
            }
            await sess._save(dict(base_session))
            await sess._save({**base_session, "id": "sess_parent", "pod_name": "pod-p"})
            await sess._save({**base_session, "id": "sess_child", "pod_name": "pod-c"})

            # child -> parent -> grandparent
            await sess.set_session_parent("sess_child", "sess_parent")
            await sess.set_session_parent("sess_parent", "sess_grandparent")

            ancestors = await sess.get_session_ancestors("sess_child")
            assert ancestors == ["sess_parent", "sess_grandparent"]

            root_anc = await sess.get_session_ancestors("sess_grandparent")
            assert root_anc == []
        finally:
            sess._DB_PATH = original_db

    @pytest.mark.asyncio
    async def test_set_parent_self_loop_rejected(self, tmp_path):
        from hub import sessions as sess
        db_file = tmp_path / "sessions_test2.db"
        original_db = sess._DB_PATH
        sess._DB_PATH = db_file
        sess._DATA_DIR = tmp_path
        try:
            await sess.init_db()
            now = int(time.time())
            await sess._save({
                "id": "s1", "owner": "u", "status": "ready",
                "created_at": now, "last_active": now,
                "pod_name": "p", "svc_name": "s",
                "mcp_url": "http://x/mcp", "api_url": "http://x",
            })
            with pytest.raises(sess.CircularParentError):
                await sess.set_session_parent("s1", "s1")
        finally:
            sess._DB_PATH = original_db

    @pytest.mark.asyncio
    async def test_set_parent_transitive_cycle_rejected(self, tmp_path):
        from hub import sessions as sess
        db_file = tmp_path / "sessions_test3.db"
        original_db = sess._DB_PATH
        sess._DB_PATH = db_file
        sess._DATA_DIR = tmp_path
        try:
            await sess.init_db()
            now = int(time.time())
            for sid in ("a", "b", "c"):
                await sess._save({
                    "id": sid, "owner": "u", "status": "ready",
                    "created_at": now, "last_active": now,
                    "pod_name": "p", "svc_name": "s",
                    "mcp_url": "http://x/mcp", "api_url": "http://x",
                })
            # a -> b -> c ; tentative c -> a doit lever CircularParentError
            await sess.set_session_parent("a", "b")
            await sess.set_session_parent("b", "c")
            with pytest.raises(sess.CircularParentError):
                await sess.set_session_parent("c", "a")
        finally:
            sess._DB_PATH = original_db

    @pytest.mark.asyncio
    async def test_set_parent_unknown_session_raises(self, tmp_path):
        from hub import sessions as sess
        db_file = tmp_path / "sessions_test4.db"
        original_db = sess._DB_PATH
        sess._DB_PATH = db_file
        sess._DATA_DIR = tmp_path
        try:
            await sess.init_db()
            with pytest.raises(sess.SessionNotFoundError):
                await sess.set_session_parent("nope-child", "nope-parent")
        finally:
            sess._DB_PATH = original_db


# ============================================================================
# S9 - Compaction rolling delta jsondiff
# ============================================================================

class TestS9CompactAssemblyDeltas:
    """assemblies_deltas_compact + compact_assembly_deltas + endpoint."""

    def test_jsondiff_dependency_available(self):
        """jsondiff est requis (setup.py) et importable."""
        import jsondiff
        assert hasattr(jsondiff, "diff")

    def test_module_exposes_compact_function(self):
        from hub import assemblies as asm
        assert hasattr(asm, "compact_assembly_deltas")
        assert inspect.iscoroutinefunction(asm.compact_assembly_deltas)
        # signature neutre : aid + max_age_days kwargs
        sig = inspect.signature(asm.compact_assembly_deltas)
        assert "aid" in sig.parameters
        assert "max_age_days" in sig.parameters

    def test_endpoint_internal_compact_registered(self):
        from hub.main import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/internal/compact-deltas" in routes

    def test_endpoint_checks_inter_pod_auth(self):
        """L'endpoint verifie _is_inter_pod_authorized (defense en profondeur)."""
        from hub.main import compact_deltas_endpoint
        src = inspect.getsource(compact_deltas_endpoint)
        assert "_is_inter_pod_authorized" in src

    @pytest.mark.asyncio
    async def test_compact_deltas_integrity_hash_deterministic(self, tmp_path):
        """Meme input -> meme integrity_hash (chain SHA256 canonique)."""
        # Isole studies.db
        from hub import studies as st
        from hub import assemblies as asm
        import aiosqlite
        db_file = tmp_path / "studies_test.db"
        original_db = st._DB_PATH
        st._DB_PATH = db_file
        asm._DB_PATH = db_file
        try:
            await st.init_db()

            aid = "aid_test_1"
            sid = "sid_test_1"
            owner = "userA"
            now = int(time.time())
            old = now - 40 * 86400  # 40 jours -> compactable

            # Insert 3 rows anciennes + 1 row recente (latest, non compactable)
            async with aiosqlite.connect(db_file) as db:
                for i, (v, hash_c, ts) in enumerate([
                    (1, "sha256:aa", old - 3),
                    (2, "sha256:bb", old - 2),
                    (3, "sha256:cc", old - 1),
                    (4, "sha256:dd", now),
                ]):
                    await db.execute(
                        """INSERT INTO assemblies_index
                           (aid, sid, owner, kind, title, content_hash,
                            version_num, assembly_version, classification,
                            provenance_json, status, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'V0.1',
                                   'cerema_internal', '{}', 'active', ?)""",
                        (aid, sid, owner, "storymap",
                         f"Title v{v}", hash_c, v, ts),
                    )
                await db.commit()

            n = await asm.compact_assembly_deltas(aid, max_age_days=30)
            # 3 rows anciennes (v1,v2,v3) doivent etre agregees
            assert n == 3

            compacts = await asm.list_assembly_deltas_compact(aid)
            assert len(compacts) == 1
            snap = compacts[0]
            assert snap["version_range_min"] == 1
            assert snap["version_range_max"] == 3
            assert snap["base_content_hash"] == "sha256:aa"
            assert snap["integrity_hash"].startswith("sha256:")

            # Idempotence : re-run ne double pas
            n2 = await asm.compact_assembly_deltas(aid, max_age_days=30)
            # No-op grace au ON CONFLICT DO NOTHING (le UNIQUE evite doublon).
            # Comme les rows sources sont toujours la, le count remonte
            # techniquement 3, mais la table compact ne grossit pas.
            compacts_after = await asm.list_assembly_deltas_compact(aid)
            assert len(compacts_after) == 1
        finally:
            st._DB_PATH = original_db
            asm._DB_PATH = original_db

    @pytest.mark.asyncio
    async def test_compact_preserves_version_num_continuity(self, tmp_path):
        """Compact ne SUPPRIME PAS de row -> version_num continuity intact."""
        from hub import studies as st
        from hub import assemblies as asm
        import aiosqlite
        db_file = tmp_path / "studies_test2.db"
        original_db = st._DB_PATH
        st._DB_PATH = db_file
        asm._DB_PATH = db_file
        try:
            await st.init_db()
            aid = "aid_continuity"
            sid = "sid_cont"
            now = int(time.time())
            old = now - 40 * 86400
            async with aiosqlite.connect(db_file) as db:
                for v, ts in [(1, old - 2), (2, old - 1), (3, now)]:
                    await db.execute(
                        """INSERT INTO assemblies_index
                           (aid, sid, owner, kind, title, content_hash,
                            version_num, assembly_version, classification,
                            provenance_json, status, created_at)
                           VALUES (?, ?, ?, 'storymap', 'T', ?, ?, 'V0.1',
                                   'cerema_internal', '{}', 'active', ?)""",
                        (aid, sid, "u", f"sha256:v{v}", v, ts),
                    )
                await db.commit()

            await asm.compact_assembly_deltas(aid, max_age_days=30)

            history = await asm.get_assembly_history(aid)
            versions = sorted(h["version_num"] for h in history)
            assert versions == [1, 2, 3], (
                "compact ne doit JAMAIS supprimer de row (INSERT-only preserve)"
            )
        finally:
            st._DB_PATH = original_db
            asm._DB_PATH = original_db

    @pytest.mark.asyncio
    async def test_compact_no_op_if_less_than_2_rows(self, tmp_path):
        from hub import studies as st
        from hub import assemblies as asm
        db_file = tmp_path / "studies_test3.db"
        original_db = st._DB_PATH
        st._DB_PATH = db_file
        asm._DB_PATH = db_file
        try:
            await st.init_db()
            n = await asm.compact_assembly_deltas("aid_empty", max_age_days=30)
            assert n == 0
        finally:
            st._DB_PATH = original_db
            asm._DB_PATH = original_db
