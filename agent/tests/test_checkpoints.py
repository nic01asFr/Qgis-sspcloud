"""Tests d'invariant : checkpoints + rollback (Commit B).

Vérifie que les fonctions memory.checkpoints respectent :
  - create + list + get + delete cohérents
  - truncate_messages_after tronque la bonne plage
  - purge_old_checkpoints applique max_per_session et max_age_days
  - L'ordre par message_idx est préservé
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

# Path setup : agent/agent/ doit être importable
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Forcer DATA_DIR temporaire AVANT l'import de memory.py
_TMP_DIR = tempfile.mkdtemp(prefix="qgis_ckpt_test_")
os.environ["DATA_DIR"] = _TMP_DIR

from agent import memory  # noqa: E402


def _run(coro):
    """Helper pour exécuter une coroutine dans un test sync."""
    return asyncio.get_event_loop().run_until_complete(coro)


async def _setup():
    await memory.init()
    await memory.create_session("s_test", "user", "standard")


def test_create_and_get_checkpoint():
    _run(_setup())
    _run(memory.create_checkpoint(
        checkpoint_id="ckpt_001",
        session_id="s_test",
        message_idx=3,
        qgz_path="/data/studies/abc/.checkpoints/ckpt_001.qgz",
        tool_name="smart_load",
        study_id="abc",
        audit_ts=1779000000.0,
    ))
    got = _run(memory.get_checkpoint("ckpt_001"))
    assert got is not None
    assert got["session_id"] == "s_test"
    assert got["message_idx"] == 3
    assert got["tool_name"] == "smart_load"
    assert got["study_id"] == "abc"


def test_list_checkpoints_ordered_by_message_idx():
    _run(_setup())
    # Insère dans un ordre random, vérifie qu'on récupère trié par msg_idx
    for i, ckpt_id in enumerate(["c3", "c1", "c2"]):
        _run(memory.create_checkpoint(
            checkpoint_id=ckpt_id, session_id="s_test",
            message_idx={"c1": 1, "c2": 2, "c3": 3}[ckpt_id],
            qgz_path=f"/tmp/{ckpt_id}.qgz",
            tool_name="x",
        ))
    ckpts = _run(memory.list_checkpoints("s_test"))
    ids = [c["id"] for c in ckpts if c["id"] in ("c1", "c2", "c3")]
    assert ids == ["c1", "c2", "c3"], f"ordre ko: {ids}"


def test_truncate_messages_after():
    """Crée 5 messages, tronque après idx 2 → reste 3 messages (les 3 premiers)."""
    _run(_setup())
    # Session dédiée pour isoler ce test
    sid = "s_trunc"
    _run(memory.create_session(sid, "user", "standard"))
    for i in range(5):
        _run(memory.add_message(sid, "user", f"msg {i}"))
    n_truncated = _run(memory.truncate_messages_after(sid, 2))
    # On garde les 3 premiers (idx 0,1,2), on supprime les 2 suivants (3,4)
    assert n_truncated == 2, f"attendu 2 supprimés, eu {n_truncated}"
    msgs = _run(memory.get_session_messages(sid, limit=20))
    contents = set(m["content"] for m in msgs)
    # Vérifie qu'on garde exactement msg 0..2 (ordre indifférent — SQLite peut
    # retourner DESC ou ASC sur timestamps identiques, ce qui n'est pas
    # l'invariant ici ; l'invariant c'est QUOI a été tronqué).
    assert contents == {"msg 0", "msg 1", "msg 2"}, f"contents={contents}"


def test_delete_checkpoint():
    _run(_setup())
    _run(memory.create_checkpoint(
        checkpoint_id="del_me", session_id="s_test", message_idx=0,
        qgz_path="/x", tool_name="t",
    ))
    assert _run(memory.get_checkpoint("del_me")) is not None
    _run(memory.delete_checkpoint("del_me"))
    assert _run(memory.get_checkpoint("del_me")) is None


def test_purge_max_per_session():
    """Crée 5 checkpoints, purge avec max=2 → garde les 2 plus récents."""
    _run(_setup())
    sid = "s_purge_max"
    _run(memory.create_session(sid, "user", "standard"))
    for i in range(5):
        _run(memory.create_checkpoint(
            checkpoint_id=f"p{i}", session_id=sid, message_idx=i,
            qgz_path=f"/p{i}.qgz", tool_name="x",
        ))
        time.sleep(0.01)
    purged = _run(memory.purge_old_checkpoints(
        session_id=sid, max_per_session=2, max_age_days=365,
    ))
    assert len(purged) == 3, f"attendu 3 purgés, eu {len(purged)}"
    # Les plus anciens (p0, p1, p2) sont purgés
    remaining = _run(memory.list_checkpoints(sid))
    remaining_ids = [c["id"] for c in remaining]
    assert "p3" in remaining_ids
    assert "p4" in remaining_ids
    assert "p0" not in remaining_ids


def test_purge_max_age():
    """Crée un checkpoint avec created_at très ancien → doit être purgé."""
    _run(_setup())
    sid = "s_purge_age"
    _run(memory.create_session(sid, "user", "standard"))
    # Insertion directe SQL avec created_at très ancien
    import aiosqlite
    async def _insert_old():
        async with aiosqlite.connect(memory._DB_PATH) as db:
            await db.execute(
                """INSERT INTO checkpoints
                   (id, session_id, message_idx, qgz_path, tool_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("old_ckpt", sid, 0, "/old.qgz", "x", 0),  # 1970
            )
            await db.commit()
    _run(_insert_old())
    purged = _run(memory.purge_old_checkpoints(
        session_id=sid, max_per_session=100, max_age_days=7,
    ))
    purged_ids = [p["id"] for p in purged]
    assert "old_ckpt" in purged_ids


if __name__ == "__main__":
    test_create_and_get_checkpoint()
    test_list_checkpoints_ordered_by_message_idx()
    test_truncate_messages_after()
    test_delete_checkpoint()
    test_purge_max_per_session()
    test_purge_max_age()
    print("OK — 6 tests passent.")
