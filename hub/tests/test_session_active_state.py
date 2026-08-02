"""Tests hub.session_active_state - Day 3 (2026-08-02).

Verifie le module de state session-scoped :
- set/get/touch/clear idempotents
- TTL applique correctement (expire = auto-purge au get)
- gc_loop purge les entrees expirees
- stats renvoie snapshot correct
- Lock asyncio serialise les concurrent writes (pas de test race directe,
  mais on verifie 2 writers parallels ne perdent aucune entree)
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest
import pytest_asyncio

from hub import session_active_state as sas


@pytest_asyncio.fixture(autouse=True)
async def reset_state():
    """Reset le state entre chaque test."""
    await sas._reset_for_tests()
    yield
    await sas._reset_for_tests()


pytestmark = pytest.mark.asyncio


# ── set/get basiques ──────────────────────────────────────────────────────────

async def test_get_absent_returns_none():
    """Session_id inconnu -> (None, None)."""
    sid, pid = await sas.get_active("unknown-mcp-sid")
    assert sid is None and pid is None


async def test_get_empty_or_none_returns_none():
    """Session_id vide/None -> (None, None) (garde absurde)."""
    assert await sas.get_active("") == (None, None)
    assert await sas.get_active(None) == (None, None)


async def test_set_then_get_roundtrip():
    """set_active suivi de get_active retourne les valeurs ecrites."""
    await sas.set_active("mcp-abc", "sid123", "pid456")
    sid, pid = await sas.get_active("mcp-abc")
    assert sid == "sid123"
    assert pid == "pid456"


async def test_set_without_pid_ok():
    """pid=None acceptable (etude sans projet default)."""
    await sas.set_active("mcp-abc", "sid123")
    sid, pid = await sas.get_active("mcp-abc")
    assert sid == "sid123"
    assert pid is None


async def test_set_sid_none_clears_entry():
    """set_active(sid=None) unset l'entree."""
    await sas.set_active("mcp-abc", "sid123", "pid456")
    await sas.set_active("mcp-abc", None)
    assert await sas.get_active("mcp-abc") == (None, None)


async def test_set_empty_session_id_noop():
    """set_active('', ...) est un no-op (garde absurde)."""
    await sas.set_active("", "sid123", "pid456")
    assert await sas.get_active("") == (None, None)


# ── TTL / expire ──────────────────────────────────────────────────────────────

async def test_get_expired_returns_none_and_purges():
    """Une entree dont expires_at < now doit etre auto-purgee au get."""
    await sas.set_active("mcp-abc", "sid123", "pid456")
    # Force expiration en manipulant directement le state (test-only)
    async with sas._lock:
        entry = sas._state["mcp-abc"]
        sas._state["mcp-abc"] = entry._replace(expires_at=time.time() - 1)
    # get doit renvoyer None ET purger l'entree
    sid, pid = await sas.get_active("mcp-abc")
    assert sid is None and pid is None
    async with sas._lock:
        assert "mcp-abc" not in sas._state


async def test_touch_prolonge_ttl():
    """touch reset expires_at a now + TTL_SECONDS."""
    await sas.set_active("mcp-abc", "sid123")
    async with sas._lock:
        old_exp = sas._state["mcp-abc"].expires_at
    await asyncio.sleep(0.01)
    await sas.touch("mcp-abc")
    async with sas._lock:
        new_exp = sas._state["mcp-abc"].expires_at
    assert new_exp > old_exp


async def test_touch_absent_noop():
    """touch sur session inconnue = no-op silencieux."""
    await sas.touch("unknown-mcp-sid")
    # Aucune entree creee
    async with sas._lock:
        assert "unknown-mcp-sid" not in sas._state


# ── clear ─────────────────────────────────────────────────────────────────────

async def test_clear_removes_entry():
    """clear() supprime l'entree explicitement."""
    await sas.set_active("mcp-abc", "sid123", "pid456")
    await sas.clear("mcp-abc")
    assert await sas.get_active("mcp-abc") == (None, None)


async def test_clear_absent_noop():
    """clear() sur session inconnue = no-op silencieux."""
    await sas.clear("unknown-mcp-sid")  # Ne throw pas


# ── gc_loop ───────────────────────────────────────────────────────────────────

async def test_gc_loop_purge_expired():
    """Un cycle gc_loop purge les entrees expirees."""
    await sas.set_active("mcp-abc", "sid-active")
    await sas.set_active("mcp-def", "sid-expired")
    # Force expiration de mcp-def
    async with sas._lock:
        entry = sas._state["mcp-def"]
        sas._state["mcp-def"] = entry._replace(expires_at=time.time() - 1)

    # Reduit intervalle gc pour test rapide, lance 1 cycle
    with patch.object(sas, "GC_INTERVAL_SECONDS", 0.01):
        task = asyncio.create_task(sas.gc_loop())
        await asyncio.sleep(0.1)  # Laisse tourner ~10 cycles
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # mcp-abc doit etre encore la, mcp-def purge
    assert await sas.get_active("mcp-abc") == ("sid-active", None)
    async with sas._lock:
        assert "mcp-def" not in sas._state


async def test_gc_loop_survives_exception():
    """gc_loop continue meme si un cycle throw.

    Simule via un lock qui throw a l'acquire au 1er appel puis fonctionne.
    """
    call_count = [0]
    original_acquire = sas._lock.acquire

    async def flaky_acquire():
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("boom simule")
        return await original_acquire()

    with patch.object(sas, "GC_INTERVAL_SECONDS", 0.01), \
         patch.object(sas._lock, "acquire", side_effect=flaky_acquire):
        task = asyncio.create_task(sas.gc_loop())
        await asyncio.sleep(0.08)  # Au moins 3 cycles : 1 throw + 2 OK
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    # Au moins un cycle apres le throw a ete tente
    assert call_count[0] >= 2


# ── stats ─────────────────────────────────────────────────────────────────────

async def test_stats_empty():
    """Aucune entree -> total et active a 0."""
    s = sas.stats()
    assert s == {"total_entries": 0, "active_entries": 0}


async def test_stats_with_active_and_expired():
    """Stats distingue actives des expirees non purgees."""
    await sas.set_active("mcp-a", "sid1")
    await sas.set_active("mcp-b", "sid2")
    await sas.set_active("mcp-c", "sid3")
    # Expire mcp-b manuellement (sans purger)
    async with sas._lock:
        entry = sas._state["mcp-b"]
        sas._state["mcp-b"] = entry._replace(expires_at=time.time() - 1)
    s = sas.stats()
    assert s["total_entries"] == 3
    assert s["active_entries"] == 2


# ── Isolation entre sessions ─────────────────────────────────────────────────

async def test_multiple_sessions_isolated():
    """2 mcp_session_id distincts -> 2 states independants."""
    await sas.set_active("mcp-A", "sidA", "pidA")
    await sas.set_active("mcp-B", "sidB", "pidB")
    assert await sas.get_active("mcp-A") == ("sidA", "pidA")
    assert await sas.get_active("mcp-B") == ("sidB", "pidB")
    # Modifier A ne touche pas B
    await sas.set_active("mcp-A", "sidA2", "pidA2")
    assert await sas.get_active("mcp-A") == ("sidA2", "pidA2")
    assert await sas.get_active("mcp-B") == ("sidB", "pidB")


async def test_concurrent_writes_no_data_loss():
    """N writers concurrents ne perdent aucune entree."""
    N = 100
    await asyncio.gather(*[
        sas.set_active(f"mcp-{i}", f"sid-{i}", f"pid-{i}")
        for i in range(N)
    ])
    for i in range(N):
        sid, pid = await sas.get_active(f"mcp-{i}")
        assert sid == f"sid-{i}"
        assert pid == f"pid-{i}"
