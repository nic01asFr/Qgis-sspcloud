"""Tests hub.main.resolve_effective_active_sid - Day 3 (2026-08-02).

Verifie la priorite de resolution : session_state > A1 legacy > DB user.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from hub import session_active_state as sas
from hub.main import resolve_effective_active_sid


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def reset_state():
    await sas._reset_for_tests()
    yield
    await sas._reset_for_tests()


# ── Priorite 1 : session_active_state (MCP moderne) ─────────────────────────

async def test_priorite_1_session_state_gagne():
    """Si session_state contient une entree, ni A1 ni DB ne sont consultes."""
    await sas.set_active("mcp-abc", "sid-session", "pid-session")
    with patch("hub.studies.get_active_study_id",
               new=AsyncMock(return_value="sid-db")), \
         patch("hub.studies.get_active_project_id",
               new=AsyncMock(return_value="pid-db")):
        sid, pid = await resolve_effective_active_sid(
            "user", mcp_session_id="mcp-abc", x_session_id="study:sid-a1",
        )
    assert sid == "sid-session"
    assert pid == "pid-session"


async def test_priorite_1_session_state_expired_fallback_a1():
    """Session expiree -> fallback A1 (session_state renvoie None)."""
    import time
    await sas.set_active("mcp-abc", "sid-session", "pid-session")
    # Force expiration
    async with sas._lock:
        e = sas._state["mcp-abc"]
        sas._state["mcp-abc"] = e._replace(expires_at=time.time() - 1)

    with patch("hub.studies.get_active_project_id",
               new=AsyncMock(return_value="pid-db")):
        sid, pid = await resolve_effective_active_sid(
            "user", mcp_session_id="mcp-abc", x_session_id="study:sid-a1",
        )
    assert sid == "sid-a1"  # A1 pattern parse
    assert pid == "pid-db"  # pid fallback DB


# ── Priorite 2 : A1 legacy ──────────────────────────────────────────────────

async def test_priorite_2_a1_gagne_si_pas_de_session_state():
    """Session_state vide + A1 valide -> A1 gagne, DB non consultee pour sid."""
    with patch("hub.studies.get_active_project_id",
               new=AsyncMock(return_value="pid-db")):
        sid, pid = await resolve_effective_active_sid(
            "user", mcp_session_id=None, x_session_id="study:sid-a1",
        )
    assert sid == "sid-a1"
    assert pid == "pid-db"  # A1 ne fournit pas pid, fallback DB pour pid seulement


async def test_priorite_2_a1_pattern_agent():
    """Pattern agent:{aid}:sid:{sid} reconnu par _extract_expected_sid."""
    with patch("hub.studies.get_active_project_id",
               new=AsyncMock(return_value=None)):
        sid, pid = await resolve_effective_active_sid(
            "user", mcp_session_id=None,
            x_session_id="agent:aid123:sid:sid-agent",
        )
    assert sid == "sid-agent"


async def test_priorite_2_a1_invalide_fallback_db():
    """A1 malforme -> _extract_expected_sid renvoie None -> fallback DB."""
    with patch("hub.studies.get_active_study_id",
               new=AsyncMock(return_value="sid-db")), \
         patch("hub.studies.get_active_project_id",
               new=AsyncMock(return_value="pid-db")):
        sid, pid = await resolve_effective_active_sid(
            "user", mcp_session_id=None, x_session_id="legacy-uuid-not-parseable",
        )
    assert sid == "sid-db"
    assert pid == "pid-db"


# ── Priorite 3 : DB user (fallback ultime) ──────────────────────────────────

async def test_priorite_3_db_seul_si_rien_d_autre():
    """Ni session_state ni A1 -> DB user."""
    with patch("hub.studies.get_active_study_id",
               new=AsyncMock(return_value="sid-db")), \
         patch("hub.studies.get_active_project_id",
               new=AsyncMock(return_value="pid-db")):
        sid, pid = await resolve_effective_active_sid(
            "user", mcp_session_id=None, x_session_id=None,
        )
    assert sid == "sid-db"
    assert pid == "pid-db"


async def test_priorite_3_db_sans_pid_ok():
    """DB avec sid mais sans pid -> (sid, None)."""
    with patch("hub.studies.get_active_study_id",
               new=AsyncMock(return_value="sid-db")), \
         patch("hub.studies.get_active_project_id",
               new=AsyncMock(return_value=None)):
        sid, pid = await resolve_effective_active_sid(
            "user", mcp_session_id=None, x_session_id=None,
        )
    assert sid == "sid-db"
    assert pid is None


# ── Cas None absolu ─────────────────────────────────────────────────────────

async def test_aucune_source_retourne_none():
    """Aucune source ne fournit -> (None, None)."""
    with patch("hub.studies.get_active_study_id",
               new=AsyncMock(return_value=None)):
        sid, pid = await resolve_effective_active_sid(
            "user", mcp_session_id=None, x_session_id=None,
        )
    assert sid is None
    assert pid is None


# ── Robustesse : DB en erreur ───────────────────────────────────────────────

async def test_db_exception_retourne_none_gracieusement():
    """DB exception -> None sans crasher."""
    with patch("hub.studies.get_active_study_id",
               new=AsyncMock(side_effect=RuntimeError("db down"))):
        sid, pid = await resolve_effective_active_sid(
            "user", mcp_session_id=None, x_session_id=None,
        )
    assert sid is None
    assert pid is None
