"""hub.session_active_state - Day 3 (2026-08-02).

Session-scoped active study/project pour clients MCP externes.

## Probleme

Depuis Day 2 (commit 8b6b427), les 6 tools MCP hub `study_*` mutent
l'`active_sid` en DB user via `hub.studies.set_active_study(username, sid)`.
Consequence : deux connexions MCP externes du meme user (ex : 2 conversations
claude.ai avec le connecteur `qgis-nic01asfr`) partagent le meme `active_sid`
DB et se marchent dessus.

## Solution

Chaque session MCP externe (identifiee par le header `Mcp-Session-Id` de la
spec MCP 2024-11-05) peut maintenir son propre couple `(active_sid, active_pid)`
dans un dict process-local avec TTL 24h.

## Priorite de resolution (voir main.py::resolve_effective_active_sid)

1. `session_active_state.get_active(mcp_session_id)` - client MCP moderne
2. `_extract_expected_sid(x_session_id)` - fallback Sprint A1 legacy
3. `studies.get_active_study_id(username)` - fallback DB user (UI desk)

## Persistance

State en memoire process : perdu au restart pod. Acceptable en pratique car
sessions MCP courtes + fallback DB propre. Un GC background purge les entrees
expirees toutes les heures.

## Rollback

Additif : retirer le module + le lookup dans mcp_auto_session = retour au
comportement Day 2.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import NamedTuple

log = logging.getLogger("hub.session_active_state")


TTL_SECONDS = 86400  # 24h : plus long que la duree typique d'une session MCP
GC_INTERVAL_SECONDS = 3600  # 1h : purge horaire suffisante


class _Entry(NamedTuple):
    sid: str | None
    pid: str | None
    expires_at: float


_state: dict[str, _Entry] = {}
_lock = asyncio.Lock()


async def set_active(
    mcp_session_id: str,
    sid: str | None,
    pid: str | None = None,
) -> None:
    """Ecrit l'etude/projet active pour cette session MCP.

    sid=None efface l'entree (unset explicite). pid=None acceptable :
    la session peut avoir une etude active sans projet default resolu.

    Idempotent : reappeler avec les memes args reset juste la TTL.
    """
    if not mcp_session_id:
        return
    async with _lock:
        if sid is None:
            _state.pop(mcp_session_id, None)
            return
        _state[mcp_session_id] = _Entry(
            sid=sid,
            pid=pid,
            expires_at=time.time() + TTL_SECONDS,
        )


async def get_active(
    mcp_session_id: str | None,
) -> tuple[str | None, str | None]:
    """Retourne (sid, pid) pour la session MCP.

    Retourne (None, None) si :
    - mcp_session_id absent/vide
    - Entree absente du dict
    - Entree expiree (auto-purge)
    """
    if not mcp_session_id:
        return None, None
    async with _lock:
        entry = _state.get(mcp_session_id)
        if not entry:
            return None, None
        if entry.expires_at < time.time():
            _state.pop(mcp_session_id, None)
            return None, None
        return entry.sid, entry.pid


async def touch(mcp_session_id: str | None) -> None:
    """Prolonge la TTL de +TTL_SECONDS. No-op si absent.

    Appele a chaque call MCP entrant pour keep-alive la session tant qu'elle
    est active. Sans touch(), une entree expire apres TTL_SECONDS d'inactivite.
    """
    if not mcp_session_id:
        return
    async with _lock:
        entry = _state.get(mcp_session_id)
        if entry:
            _state[mcp_session_id] = entry._replace(
                expires_at=time.time() + TTL_SECONDS,
            )


async def clear(mcp_session_id: str | None) -> None:
    """Efface explicitement une entree (ex : deconnexion client MCP).

    Alias de set_active(mcp_session_id, None). No-op si absent.
    """
    if not mcp_session_id:
        return
    async with _lock:
        _state.pop(mcp_session_id, None)


async def gc_loop() -> None:
    """Task background : purge entrees expirees toutes les GC_INTERVAL_SECONDS.

    Lance depuis mcp_auto_session startup (@app.on_event("startup")). Boucle
    infinie robuste : toute exception dans un cycle est loggee mais ne casse
    pas la boucle.
    """
    while True:
        try:
            await asyncio.sleep(GC_INTERVAL_SECONDS)
            now = time.time()
            async with _lock:
                expired = [k for k, v in _state.items() if v.expires_at < now]
                for k in expired:
                    _state.pop(k, None)
            if expired:
                log.info(
                    "gc_loop: purge %d entrees expirees (restantes: %d)",
                    len(expired), len(_state),
                )
        except asyncio.CancelledError:
            log.info("gc_loop: cancelled (shutdown hub)")
            raise
        except Exception as exc:
            log.warning("gc_loop: cycle en erreur (continue) : %s", exc)


def stats() -> dict:
    """Snapshot pour endpoint /debug/iso-metrics.

    Retourne :
    - total_entries : nombre d'entrees en memoire (actives + expirees non purgees)
    - active_entries : entrees non expirees a l'instant t

    Ne prend PAS le lock : lecture best-effort pour observability, une legere
    inconsistance est acceptable (les valeurs peuvent varier de +/-1 sur un
    read concurrent).
    """
    now = time.time()
    total = len(_state)
    active = sum(1 for v in _state.values() if v.expires_at >= now)
    return {"total_entries": total, "active_entries": active}


# Testing helpers (utilises par tests unit uniquement) ────────────────────────

async def _reset_for_tests() -> None:
    """Reset complet du state (usage tests unitaires exclusif)."""
    async with _lock:
        _state.clear()
