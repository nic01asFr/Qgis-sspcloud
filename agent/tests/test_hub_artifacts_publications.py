"""L2c inclut les publications catalogue avec hub_url complète.

Sans ça l'agent ne voit que composants/assemblages V1.5 (souvent vides)
et propose de republier sur S3.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TMP_DIR = tempfile.mkdtemp(prefix="qgis_l2c_pub_")
os.environ["DATA_DIR"] = _TMP_DIR

from agent import memory  # noqa: E402
from agent.hub_artifacts import (  # noqa: E402
    build_next_action_hints,
    summarize_artifacts,
)

memory._DATA_DIR = Path(_TMP_DIR)
memory._DB_PATH = Path(_TMP_DIR) / "memory_l2c_pub.db"

_HUB = "https://user-nic01asfr-qgis.user.lab.sspcloud.fr"
_SCENE_URL = f"{_HUB}/published/nic01asfr/features/scene-sxm-eolien"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_summarize_keeps_full_hub_url():
    raw = {
        "components": [],
        "assemblies": [],
        "publications": [{
            "slug": "scene-sxm-eolien",
            "kind": "features",
            "hub_url": _SCENE_URL,
            "audience": "public",
            "published_at": 1,
        }],
    }
    s = summarize_artifacts(raw)
    rec = s["publications"]["recent"][0]
    assert rec["hub_url"] == _SCENE_URL
    assert rec["slug"] == "scene-sxm-eolien"
    assert rec["kind"] == "features"
    assert s["publications"]["total"] == 1
    assert "aid" not in rec


def test_summarize_omits_minio_url():
    raw = {
        "components": [],
        "assemblies": [],
        "publications": [{
            "slug": "x",
            "kind": "dataset",
            "url": "https://minio.lab.sspcloud.fr/u/qgis-workspace/published/u/dataset/x.gpkg",
            "hub_url": f"{_HUB}/published/u/dataset/x",
        }],
    }
    rec = summarize_artifacts(raw)["publications"]["recent"][0]
    assert "minio" not in rec.get("hub_url", "")
    assert "url" not in rec


def test_hint_says_do_not_republish_when_publications_exist():
    art = summarize_artifacts({
        "components": [],
        "assemblies": [],
        "publications": [{
            "slug": "a",
            "kind": "storymap",
            "hub_url": f"{_HUB}/published/u/storymap/a",
        }],
    })
    hints = build_next_action_hints(art)
    blob = " ".join(hints).lower()
    assert "hub_url" in blob
    assert any("republi" in h.lower() for h in hints)
    assert "n'utilise jamais minio.lab.sspcloud.fr" in blob
    assert "https://minio." not in blob


def test_l2_lists_full_publication_url():
    _run(memory.init())
    out = _run(memory.build_context_summary(
        username="user",
        session_id="study:49bdd6db6d6b",
        active_study={"id": "49bdd6db6d6b", "name": "Saint-Martin"},
        study_artifacts=summarize_artifacts({
            "components": [],
            "assemblies": [],
            "publications": [{
                "slug": "scene-sxm-eolien",
                "kind": "features",
                "hub_url": _SCENE_URL,
            }],
        }),
    ))
    assert "Livrables publiés" in out
    assert _SCENE_URL in out
    assert "scene-sxm-eolien" in out


def test_brief_counts_catalog_publications():
    lines = memory._brief_study_summary(
        {"name": "Saint-Martin"},
        summarize_artifacts({
            "components": [],
            "assemblies": [],
            "publications": [{
                "slug": "a", "kind": "dataset",
                "hub_url": f"{_HUB}/published/u/dataset/a",
            }],
        }),
    )
    blob = " ".join(lines)
    assert "1 livrable" in blob.lower() or "publié" in blob.lower()
