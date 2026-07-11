"""Test : reecriture URLs pod-internes -> URL hub publique.

Bug 2026-07-11 : upload_file MCP renvoyait `http://localhost:8080/api/files/X`
dans certaines branches du wrapper qgis_agent._call_mcp_tool_raw. Depuis
Windows l'user cliquait un lien mort. Le fix factorise la reecriture dans
`_rewrite_workspace_urls` et l'applique aux DEUX branches de retour (texte
concatene ET fallback JSON structure).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("HUB_URL", "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr")

from agent import qgis_agent  # noqa: E402


def test_reecriture_localhost_port_standard():
    payload = "Fichier uploade : http://localhost:8080/api/files/rapport.pdf"
    out = qgis_agent._rewrite_workspace_urls(payload)
    assert "localhost" not in out
    assert out.endswith("/files/rapport.pdf")
    assert out.startswith("Fichier uploade : https://user-nicolaslaval-qgis")


def test_reecriture_localhost_port_variable():
    for port in (8080, 8100, 8888, 12345):
        payload = f"http://localhost:{port}/api/files/x.gpkg"
        out = qgis_agent._rewrite_workspace_urls(payload)
        assert "localhost" not in out, f"port {port} non reecrit"


def test_reecriture_127_0_0_1():
    payload = "http://127.0.0.1:8080/api/files/z.tif"
    out = qgis_agent._rewrite_workspace_urls(payload)
    assert "127.0.0.1" not in out
    assert out.endswith("/files/z.tif")


def test_reecriture_json_structure():
    payload = '{"status": "ok", "url": "http://localhost:8080/api/files/livrable.pdf"}'
    out = qgis_agent._rewrite_workspace_urls(payload)
    assert "localhost:8080" not in out
    assert "/files/livrable.pdf" in out


def test_idempotent():
    payload = "http://localhost:8080/api/files/a.pdf"
    once = qgis_agent._rewrite_workspace_urls(payload)
    twice = qgis_agent._rewrite_workspace_urls(once)
    assert once == twice


def test_payload_vide_ou_none():
    assert qgis_agent._rewrite_workspace_urls("") == ""


def test_pas_de_faux_positif_sur_url_publique():
    payload = "Voir https://data.gouv.fr/api/files/dataset.zip"
    out = qgis_agent._rewrite_workspace_urls(payload)
    # data.gouv n'est ni localhost ni 127.0.0.1, ne doit pas etre reecrit
    assert out == payload


def test_reecriture_api_upload():
    # Cas reel upload_file : retourne /api/upload endpoint
    payload = '{"action":"upload_ready","upload_endpoint":"http://localhost:8080/api/upload","method":"POST"}'
    out = qgis_agent._rewrite_workspace_urls(payload)
    assert "localhost:8080" not in out
    assert "/api/upload" in out
    assert qgis_agent._HUB_URL + "/api/upload" in out


def test_reecriture_mixte_upload_et_files_dans_meme_payload():
    payload = (
        "Upload : http://localhost:8080/api/upload\n"
        "Puis download : http://localhost:8080/api/files/rapport.pdf"
    )
    out = qgis_agent._rewrite_workspace_urls(payload)
    assert "localhost" not in out
    assert "/api/upload" in out  # upload preserve
    assert "/files/rapport.pdf" in out  # files rewrite -> /files/


def test_pas_de_reecriture_endpoint_inconnu():
    # Un endpoint pas dans la whitelist ne doit pas etre reecrit
    payload = "http://localhost:8080/api/random_endpoint"
    out = qgis_agent._rewrite_workspace_urls(payload)
    assert out == payload  # pas dans le regex, reste tel quel
