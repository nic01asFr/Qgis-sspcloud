"""Tests Sprint V0.4.2 Chantier B : assembly_bridge.

Verifie la convergence Assembly : le scene_manifest produit par
`execute_recipe_polished` se materialise en (Assembly + [Component]) persistes
dans assemblies_index + components_index -- meme modele que la voie agent
chat (Marie V3).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Isolation DB studies AVANT import.
_TMP_DIR = tempfile.mkdtemp(prefix="qgis_v042_bridge_")
os.environ["DATA_DIR"] = _TMP_DIR
os.environ.setdefault("HUB_URL", "http://localhost:8888")
os.environ.setdefault("HUB_API_KEY", "test-key")
os.environ.setdefault("ONYXIA_USER", "test-user")

from hub import studies  # noqa: E402
from hub.recipes_web.assembly_bridge import (  # noqa: E402
    _build_component_from_block,
    _extract_narrative_blocks,
    _new_id,
    create_assembly_from_scene_manifest,
)

studies._DB_PATH = Path(_TMP_DIR) / "studies.db"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _init_db():
    """Init tables studies (assemblies_index + components_index)."""
    _run(studies.init_db())
    yield


def _make_manifest(n_narrative: int = 1, with_map: bool = True) -> dict:
    """Fabrique un scene_manifest V0.3.1 avec narrative + optionnel map."""
    comps: list[dict] = []
    for i in range(n_narrative):
        comps.append({
            "id": f"nt_{i}",
            "kind": "narrative_text",
            "content": f"Paragraphe {i} du livrable CEREMA.",
        })
    if with_map:
        comps.append({
            "id": "map_1",
            "kind": "interactive_map",
            "title": "Carte parc bati Marseille 4e",
            "layers": [{"id": "batiments"}],
        })
    return {
        "$schema": "https://cerema.github.io/geo-components/schemas/scene_manifest/0.3.1.json",
        "manifest_version": "0.3.1",
        "produced_at": "2026-07-14T10:00:00Z",
        "title": "Diagnostic parc bati Marseille 4e",
        "provenance": {
            "producer": "qgis-sspcloud/recipes_web",
            "recipe_used": {"slug": "diagnostic_parc_bati", "version": 1},
            "sources": [
                {"corpus": "BD TOPO", "authority": "IGN", "millesime": "2024"},
            ],
        },
        "components": comps,
    }


# ── 1. _new_id : 12-hex conforme pattern Pydantic ───────────────────────────


def test_new_id_matches_pattern():
    aid = _new_id()
    assert len(aid) == 12
    assert all(c in "0123456789abcdef" for c in aid)


# ── 2. _extract_narrative_blocks : filtre kind ──────────────────────────────


def test_extract_blocks_from_components_key():
    manifest = _make_manifest(n_narrative=2, with_map=True)
    blocks = _extract_narrative_blocks(manifest)
    assert len(blocks) == 3  # 2 narrative_text + 1 interactive_map


def test_extract_blocks_from_narrative_key():
    """Variante `narrative` (V0.3.1 accepte les 2)."""
    manifest = {
        "narrative": [
            {"id": "n_1", "kind": "narrative_text", "content": "hello"},
        ],
    }
    blocks = _extract_narrative_blocks(manifest)
    assert len(blocks) == 1


def test_extract_blocks_skip_non_dict():
    manifest = {"components": ["not a dict", None, {"kind": "narrative_text"}]}
    blocks = _extract_narrative_blocks(manifest)
    assert len(blocks) == 1


# ── 3. _build_component_from_block : mapping kind ───────────────────────────


def test_build_component_narrative_text():
    block = {"kind": "narrative_text", "content": "Texte du livrable"}
    comp = _build_component_from_block(
        block, sid="a" * 12, scene_manifest_url=None,
    )
    assert comp is not None
    assert comp.kind == "narrative_text"
    assert comp.source is None  # narrative_text pas de source data
    assert comp.rendering.runtime == "marked"


def test_build_component_interactive_map_has_source():
    block = {"kind": "interactive_map", "title": "Carte"}
    comp = _build_component_from_block(
        block, sid="b" * 12, scene_manifest_url="/studies/xxx/scene",
    )
    assert comp is not None
    assert comp.kind == "interactive_map"
    assert comp.source is not None
    assert comp.source.scope == "study"
    assert comp.source.sid == "b" * 12


def test_build_component_unknown_kind_returns_none():
    block = {"kind": "unknown_kind_xyz"}
    comp = _build_component_from_block(
        block, sid="c" * 12, scene_manifest_url=None,
    )
    assert comp is None


# ── 4. create_assembly_from_scene_manifest : persistance E2E ─────────────────


def test_create_assembly_persists_assembly_and_components():
    sid = "0123456789ab"
    manifest = _make_manifest(n_narrative=2, with_map=True)
    aid, cids = _run(
        create_assembly_from_scene_manifest(
            scene_manifest=manifest,
            sid=sid,
            owner="test-user",
        )
    )
    assert len(aid) == 12
    assert len(cids) == 3  # 2 narrative_text + 1 interactive_map
    for cid in cids:
        assert len(cid) == 12
    # Verifier lookup Assembly
    from hub import assemblies
    latest = _run(assemblies.get_assembly_latest(aid))
    assert latest is not None
    assert latest["sid"] == sid
    assert latest["kind"] == "storymap_narrative_dsfr"


def test_create_assembly_uses_manifest_title():
    sid = "1111ffffabcd"
    manifest = _make_manifest(n_narrative=1)
    aid, _ = _run(
        create_assembly_from_scene_manifest(
            scene_manifest=manifest,
            sid=sid,
            owner="test-user",
        )
    )
    from hub import assemblies
    latest = _run(assemblies.get_assembly_latest(aid))
    assert latest["title"] == "Diagnostic parc bati Marseille 4e"


def test_create_assembly_title_override_wins():
    sid = "2222ffffabcd"
    manifest = _make_manifest(n_narrative=1)
    aid, _ = _run(
        create_assembly_from_scene_manifest(
            scene_manifest=manifest,
            sid=sid,
            owner="test-user",
            title_override="Titre custom",
        )
    )
    from hub import assemblies
    latest = _run(assemblies.get_assembly_latest(aid))
    assert latest["title"] == "Titre custom"


def test_create_assembly_rejects_invalid_sid():
    """sid non-12-hex -> ValueError."""
    manifest = _make_manifest(n_narrative=1)
    with pytest.raises(ValueError, match="sid invalide"):
        _run(
            create_assembly_from_scene_manifest(
                scene_manifest=manifest,
                sid="not-hex",
                owner="test-user",
            )
        )


def test_create_assembly_empty_manifest_still_creates_assembly():
    """Un manifest sans components produit un Assembly vide (fail-soft)."""
    sid = "3333ffffabcd"
    manifest = {"manifest_version": "0.3.1", "title": "Empty"}
    aid, cids = _run(
        create_assembly_from_scene_manifest(
            scene_manifest=manifest,
            sid=sid,
            owner="test-user",
        )
    )
    assert len(aid) == 12
    assert cids == []
    from hub import assemblies
    latest = _run(assemblies.get_assembly_latest(aid))
    assert latest is not None
