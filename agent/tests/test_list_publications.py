"""list_publications ne renvoie que hub_url, jamais le locateur MinIO."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("ONYXIA_USER", "nic01asfr")

from agent import native_tools_v2 as nt  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_list_publications_strips_minio_and_filters_study():
    catalog = {
        "items": [
            {
                "slug": "scene-sxm-eolien",
                "kind": "features",
                "study_id": "49bdd6db6d6b",
                "audience": "public",
                "url": "https://minio.lab.sspcloud.fr/nic01asfr/x.geojson",
                "hub_url": "https://hub.example/published/nic01asfr/features/scene-sxm-eolien",
            },
            {
                "slug": "other-study",
                "kind": "dataset",
                "study_id": "aaaaaaaaaaaa",
                "hub_url": "https://hub.example/published/nic01asfr/dataset/other-study",
            },
            {
                "slug": "no-hub",
                "kind": "pdf",
                "study_id": "49bdd6db6d6b",
                "url": "https://minio.lab.sspcloud.fr/nic01asfr/y.pdf",
            },
        ]
    }
    with patch.object(nt, "_hub_call", new=AsyncMock(return_value=catalog)):
        out = _run(nt.list_publications(study_id="49bdd6db6d6b"))
    assert out["count"] == 1
    item = out["items"][0]
    assert item["slug"] == "scene-sxm-eolien"
    assert "url" not in item
    assert "minio" not in item["hub_url"]


def test_list_publications_without_onyxia_user():
    with patch.object(nt.os, "getenv", return_value=""):
        out = _run(nt.list_publications())
    assert "error" in out


def test_qgis_agent_invalidates_l2c_after_publish_artifact():
    src = (_ROOT / "agent" / "qgis_agent.py").read_text(encoding="utf-8")
    assert 'if fn_name == "publish_artifact":' in src
    assert "_artifacts_force_refresh = True" in src
    assert "list_publications" in src
    assert "Ne republie" in src
    assert "JAMAIS pour obtenir un lien" in src
    assert "minio.lab.sspcloud.fr" in src
    assert "3 familles" in src
    assert "/studies/{sid}/file/{relpath}" in src
    assert "tu veux que je procède" in src
    assert ".aux.xml" in src
    assert "CE TOUR" in src
    assert "kind=features" in src
    assert "guard_publish_artifact" in src
    assert "ALREADY_PUBLISHED" in src
    assert 'if "ALREADY_PUBLISHED" in (result or "")' in src
    assert "exception republish" in src
    assert "{HUB_URL}/studies/{sid}/file/{relpath}" in src


def _items():
    return [
        {
            "slug": "scene-sxm-eolien",
            "kind": "features",
            "hub_url": "https://hub.example/published/u/features/scene-sxm-eolien",
        }
    ]


def test_refuse_republish_same_slug_other_kind():
    out = nt.refuse_republish_if_exists("scene-sxm-eolien", "dataset", _items())
    assert out["error"] == "ALREADY_PUBLISHED"
    assert out["existing_kind"] == "features"
    assert out["hub_url"].endswith("/features/scene-sxm-eolien")
    assert "publish_artifact" in out["instruction"]


def test_refuse_republish_v_suffix_of_existing_slug():
    out = nt.refuse_republish_if_exists("scene-sxm-eolien-v2", "dataset", _items())
    assert out["error"] == "ALREADY_PUBLISHED"
    assert out["hub_url"].endswith("/features/scene-sxm-eolien")


def test_allow_publish_unrelated_slug():
    assert nt.refuse_republish_if_exists("autre-carte", "dataset", _items()) is None
