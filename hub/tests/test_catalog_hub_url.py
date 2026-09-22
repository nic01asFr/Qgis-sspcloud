"""Le catalogue expose hub_url au moment du GET, pas l'URL MinIO comme lien.

Constate en live 2026-09-22 : GET /catalog et /studies/{sid}/publications
renvoient url=minio (403). Seul /desk/catalog ajoutait hub_url. L'agent
n'a donc jamais le lien partageable.
"""

from __future__ import annotations

from pathlib import Path

import hub.s3_publication as s3

_MAIN = Path(__file__).resolve().parents[1] / "hub" / "main.py"


def test_hub_url_for_strips_slash_and_extension_passthrough():
    url = s3.hub_url_for(
        "nic01asfr", "features", "scene-sxm-eolien",
        "https://user-nic01asfr-qgis.user.lab.sspcloud.fr/",
    )
    assert url == (
        "https://user-nic01asfr-qgis.user.lab.sspcloud.fr"
        "/published/nic01asfr/features/scene-sxm-eolien"
    )


def test_hub_url_for_strips_file_extension_from_slug():
    url = s3.hub_url_for(
        "nic01asfr", "dataset", "test-chrome-20260922.gpkg",
        "https://hub.example",
    )
    assert url.endswith("/published/nic01asfr/dataset/test-chrome-20260922")
    assert not url.endswith(".gpkg")


def test_enrich_adds_hub_url_keeps_minio_locator():
    item = {
        "owner": "nic01asfr", "kind": "dataset", "slug": "test-chrome-20260922",
        "url": "https://minio.lab.sspcloud.fr/nic01asfr/qgis-workspace/published/"
               "nic01asfr/dataset/test-chrome-20260922.gpkg",
    }
    out = s3.enrich_catalog_item(
        item, "https://user-nic01asfr-qgis.user.lab.sspcloud.fr",
    )
    assert out["url"].startswith("https://minio.")
    assert out["hub_url"].endswith(
        "/published/nic01asfr/dataset/test-chrome-20260922"
    )
    assert "hub_url" not in item


def test_enrich_skips_incomplete_item():
    out = s3.enrich_catalog_item({"slug": "x"}, "https://hub.example")
    assert "hub_url" not in out


def test_enrich_does_not_overwrite_existing_hub_url():
    item = {
        "owner": "u", "kind": "storymap", "slug": "a",
        "hub_url": "https://already.example/published/u/storymap/a",
    }
    out = s3.enrich_catalog_item(item, "https://other.example")
    assert out["hub_url"] == "https://already.example/published/u/storymap/a"


def _fn_body(src: str, name: str) -> str:
    marker = f"async def {name}"
    chunk = src.split(marker, 1)
    assert len(chunk) == 2, f"{name} introuvable"
    rest = chunk[1]
    nxt = rest.find("\nasync def ")
    return rest if nxt < 0 else rest[:nxt]


def test_catalog_endpoints_call_enrich():
    src = _MAIN.read_text(encoding="utf-8")
    assert src.count("enrich_catalog_item") >= 4
    assert "enrich_catalog_item" in _fn_body(src, "get_owner_catalog")
    assert "enrich_catalog_item" in _fn_body(src, "list_study_publications")
    assert "enrich_catalog_item" in _fn_body(src, "desk_catalog")
