"""Tests d'invariant : session tags + parse_session_id (Chantier G1).

Verifie :
  - parse_session_id sur les 6 patterns + fallback legacy
  - set/get/find sur un tag simple, puis multi-tag
  - find_sessions_by_tags applique bien l'intersection (AND)
  - create_session auto-tag une session structuree (context_kind + sid + cid)
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Path setup : agent/agent/ doit etre importable
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Forcer DATA_DIR temporaire AVANT l'import de memory.py.
# NB : les autres tests du repo (test_checkpoints) posent aussi un DATA_DIR
# via mkdtemp — la variable env est ecrasee au chargement du module. On
# force ici la reinitialisation via memory._DB_PATH.
_TMP_DIR = tempfile.mkdtemp(prefix="qgis_tags_test_")
os.environ["DATA_DIR"] = _TMP_DIR

from agent import memory  # noqa: E402

# Assurer que le DB_PATH pointe bien sur notre tempdir (le module a pu etre
# importe avec un DATA_DIR precedent par un autre test dans la meme session).
memory._DATA_DIR = Path(_TMP_DIR)
memory._DB_PATH = Path(_TMP_DIR) / "memory.db"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _setup():
    await memory.init()


# ── parse_session_id ─────────────────────────────────────────────────────────

def test_parse_desk():
    got = memory.parse_session_id("study:c6c123")
    assert got == {"context_kind": "desk", "sid": "c6c123"}


def test_parse_assist_component():
    got = memory.parse_session_id("study:c6c:cid:abc")  # ancien format ?
    # ancien format non supporte : c'est assist:{sid}:cid:{cid}
    assert got == {"context_kind": "legacy"}

    got = memory.parse_session_id("assist:c6c:cid:abc")
    assert got == {
        "context_kind": "assist_component",
        "sid": "c6c",
        "cid": "abc",
    }


def test_parse_assist_assembly():
    got = memory.parse_session_id("assist:sid42:aid:asm7")
    assert got == {
        "context_kind": "assist_assembly",
        "sid": "sid42",
        "aid": "asm7",
    }


def test_parse_editor_freeform():
    got = memory.parse_session_id("study:s1:draft:draft-xyz")
    assert got == {
        "context_kind": "editor_freeform",
        "sid": "s1",
        "draft_id": "draft-xyz",
    }


def test_parse_recipe_run():
    got = memory.parse_session_id("study:s1:recipe:r_choropleth")
    assert got == {
        "context_kind": "recipe_run",
        "sid": "s1",
        "recipe_id": "r_choropleth",
    }


def test_parse_recipe_create():
    got = memory.parse_session_id("study:s1:recipe_edit:my-slug")
    assert got == {
        "context_kind": "recipe_create",
        "sid": "s1",
        "recipe_id": "my-slug",
    }


def test_parse_legacy_uuid():
    # UUID pur historique
    got = memory.parse_session_id("d5e6f7a8-b9c0-1234-5678-9abcdef01234")
    assert got == {"context_kind": "legacy"}


def test_parse_empty_or_garbage():
    assert memory.parse_session_id("") == {"context_kind": "legacy"}
    assert memory.parse_session_id("study:") == {"context_kind": "legacy"}
    assert memory.parse_session_id("assist:sid:foo:bar") == {"context_kind": "legacy"}


# ── set / get / find ─────────────────────────────────────────────────────────

def test_set_get_tag_roundtrip():
    _run(_setup())
    sid = "sess_a"
    _run(memory.set_session_tag(sid, "cid", "comp1"))
    _run(memory.set_session_tag(sid, "context_kind", "assist_component"))
    tags = _run(memory.get_session_tags(sid))
    assert tags == {"cid": "comp1", "context_kind": "assist_component"}


def test_set_tag_overwrite():
    _run(_setup())
    sid = "sess_overwrite"
    _run(memory.set_session_tag(sid, "cid", "v1"))
    _run(memory.set_session_tag(sid, "cid", "v2"))
    tags = _run(memory.get_session_tags(sid))
    assert tags == {"cid": "v2"}


def test_find_sessions_by_tag():
    _run(_setup())
    _run(memory.set_session_tag("s_a", "cid", "shared"))
    _run(memory.set_session_tag("s_b", "cid", "shared"))
    _run(memory.set_session_tag("s_c", "cid", "other"))
    found = _run(memory.find_sessions_by_tag("cid", "shared"))
    assert set(found) == {"s_a", "s_b"}
    assert "s_c" not in found


def test_find_sessions_by_tags_intersection():
    _run(_setup())
    # s1 : cid=X + kind=assist_component
    _run(memory.set_session_tag("s1", "cid", "X"))
    _run(memory.set_session_tag("s1", "context_kind", "assist_component"))
    # s2 : cid=X mais kind=other
    _run(memory.set_session_tag("s2", "cid", "X"))
    _run(memory.set_session_tag("s2", "context_kind", "other"))
    # s3 : kind=assist_component mais cid=Y
    _run(memory.set_session_tag("s3", "cid", "Y"))
    _run(memory.set_session_tag("s3", "context_kind", "assist_component"))

    found = _run(memory.find_sessions_by_tags({
        "cid": "X",
        "context_kind": "assist_component",
    }))
    assert found == ["s1"]


def test_find_sessions_by_tags_empty_dict():
    _run(_setup())
    assert _run(memory.find_sessions_by_tags({})) == []


# ── Integration : create_session auto-tag ────────────────────────────────────

def test_create_session_auto_tags_convention():
    _run(_setup())
    sid = "assist:study9:cid:comp42"
    _run(memory.create_session(sid, "user", "standard"))
    tags = _run(memory.get_session_tags(sid))
    assert tags["context_kind"] == "assist_component"
    assert tags["sid"] == "study9"
    assert tags["cid"] == "comp42"


def test_create_session_legacy_still_tagged_legacy():
    _run(_setup())
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    _run(memory.create_session(sid, "user", "standard"))
    tags = _run(memory.get_session_tags(sid))
    assert tags == {"context_kind": "legacy"}


def test_set_session_study_backfills_sid_tag():
    """Session legacy (UUID) associee a une etude -> sid tag ajoute."""
    _run(_setup())
    sid = "legacy-uuid-xxxxxxx"
    _run(memory.create_session(sid, "user", "standard"))
    _run(memory.set_session_study(sid, "study_abc"))
    tags = _run(memory.get_session_tags(sid))
    assert tags.get("sid") == "study_abc"
    # context_kind reste legacy pour une session UUID sans convention
    assert tags.get("context_kind") == "legacy"


if __name__ == "__main__":
    # Run tous les tests manuellement (utile pour debug hors pytest).
    import inspect
    ns = dict(globals())
    for name, fn in ns.items():
        if name.startswith("test_") and callable(fn):
            print(f"→ {name}")
            fn()
    print("OK — session tags tests passent.")
