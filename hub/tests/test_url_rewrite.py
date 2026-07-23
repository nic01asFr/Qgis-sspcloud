"""Tests hub.url_rewrite — port du pattern agent cote hub (2026-07-23).

Verifie que les URLs pod-interne `localhost:PORT/api/...` sont bien
reecrites vers l'URL hub publique, dans les 2 branches ciblees
(/api/files/X -> /files/X, /api/upload -> /api/upload).

Equivalent au test agent/tests/test_rewrite_workspace_urls.py, adapte
a la signature hub.url_rewrite.rewrite_workspace_urls(payload, hub_url).
"""
from __future__ import annotations

import pytest

from hub.url_rewrite import rewrite_workspace_urls


HUB_URL = "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr"


def test_upload_localhost_rewritten():
    """URL localhost:8080/api/upload -> hub public /api/upload."""
    payload = "Endpoint: http://localhost:8080/api/upload"
    out = rewrite_workspace_urls(payload, HUB_URL)
    assert "localhost" not in out
    assert f"{HUB_URL}/api/upload" in out


def test_upload_127_rewritten():
    """URL 127.0.0.1:8080/api/upload -> hub public /api/upload."""
    payload = "Endpoint: http://127.0.0.1:8080/api/upload"
    out = rewrite_workspace_urls(payload, HUB_URL)
    assert "127.0.0.1" not in out
    assert f"{HUB_URL}/api/upload" in out


def test_files_localhost_rewritten():
    """URL localhost:8080/api/files/X -> hub public /files/X (change prefixe)."""
    payload = "Download: http://localhost:8080/api/files/data.geojson"
    out = rewrite_workspace_urls(payload, HUB_URL)
    assert "localhost" not in out
    assert f"{HUB_URL}/files/data.geojson" in out
    # Pas de /api/files/... dans la sortie (le prefixe change)
    assert "/api/files/" not in out


def test_files_nested_path_rewritten():
    """URL localhost:8080/api/files/nested/subdir/file.gpkg -> nested path preserve."""
    payload = "http://localhost:8080/api/files/exports/2026-07-21/aleas.gpkg"
    out = rewrite_workspace_urls(payload, HUB_URL)
    assert f"{HUB_URL}/files/exports/2026-07-21/aleas.gpkg" in out


def test_idempotent():
    """Rewrite deja fait = no-op (regex ne matche pas hub public)."""
    payload = "Upload: http://localhost:8080/api/upload"
    once = rewrite_workspace_urls(payload, HUB_URL)
    twice = rewrite_workspace_urls(once, HUB_URL)
    assert once == twice
    # Coherence : deja rewrite = pas de localhost
    assert "localhost" not in twice


def test_no_op_empty_payload():
    """Payload vide = retour tel quel (fail-safe)."""
    assert rewrite_workspace_urls("", HUB_URL) == ""


def test_no_op_no_urls():
    """Payload sans URLs workspace = retour tel quel."""
    payload = "no url here, just some text"
    assert rewrite_workspace_urls(payload, HUB_URL) == payload


def test_no_op_empty_hub_url():
    """Hub URL vide = fail-safe, ne casse rien."""
    payload = "Upload: http://localhost:8080/api/upload"
    assert rewrite_workspace_urls(payload, "") == payload


def test_mixed_urls_only_workspace_touched():
    """Autres URLs (S3, IGN, tile OSM) ne sont PAS touchees."""
    payload = (
        "workspace=http://localhost:8080/api/upload, "
        "s3=https://minio.lab.sspcloud.fr/nicolaslaval/data.geojson, "
        "osm=https://tile.openstreetmap.org/13/4171/2989.png"
    )
    out = rewrite_workspace_urls(payload, HUB_URL)
    # Workspace URL reecrite
    assert f"{HUB_URL}/api/upload" in out
    # Autres URLs preservees
    assert "https://minio.lab.sspcloud.fr/nicolaslaval/data.geojson" in out
    assert "https://tile.openstreetmap.org/13/4171/2989.png" in out


def test_json_payload_rewritten():
    """Fonctionne dans un payload JSON serialise (cas reel tools/call reponse)."""
    payload = (
        '{"jsonrpc":"2.0","result":{"content":[{"type":"text",'
        '"text":"Upload endpoint: http://localhost:8080/api/upload"}]}}'
    )
    out = rewrite_workspace_urls(payload, HUB_URL)
    assert "localhost:8080" not in out
    assert f"{HUB_URL}/api/upload" in out
