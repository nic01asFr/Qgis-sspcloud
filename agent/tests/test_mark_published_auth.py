"""Tests fix H2 revue adversariale Sprint V0.4.1 : mark_published exige
un published_url dans un domaine whitelist et une auth inter-pod.

Avant fix : le endpoint acceptait n'importe quel published_url et etait
whitelist via UA=kube-probe seul -> attaquant Internet marquait un livrable
arbitraire avec une URL de phishing.

Apres fix :
  - Route ``/journal/livrables`` ajoutee a ``_AGENT_INTER_POD_ROUTES``
    -> exige Bearer HUB_API_KEY (defense in depth).
  - Regex sur ``published_url`` : doit pointer vers un domaine legitime
    (*.user.lab.sspcloud.fr, *.gouv.fr, *.cerema.fr).
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

# Isolation DATA_DIR AVANT import memory (patron des autres tests agent).
_TMP_DIR = tempfile.mkdtemp(prefix="qgis_h2_test_")
os.environ["DATA_DIR"] = _TMP_DIR

# Stub sqlite_vec (dep optionnelle).
if "sqlite_vec" not in sys.modules:
    _vec_stub = type(sys)("sqlite_vec")
    _vec_stub.load = lambda *a, **k: None  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec_stub

os.environ.setdefault("HUB_URL", "https://hub.example.com")
os.environ["HUB_API_KEY"] = "test-hub-key-h2"
os.environ.setdefault("QGIS_API_KEY", "test-hub-key-h2")
os.environ.setdefault("ONYXIA_USER", "test-user")

from fastapi.testclient import TestClient  # noqa: E402

from agent import memory  # noqa: E402
from agent.main import _AGENT_INTER_POD_ROUTES, app  # noqa: E402

memory._DATA_DIR = Path(_TMP_DIR)
memory._DB_PATH = Path(_TMP_DIR) / "memory_h2.db"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_livrable() -> str:
    _run(memory.init())
    return _run(memory.journal_livrable(
        session_id="study:sH2:recipe:demo",
        username="marie",
        kind="recipe_pure",
        output_hash="cafebabe",
        recipe_id="demo",
        context_kind="recipe_run",
        briques_used=[],
        metadata={},
    ))


def test_mark_published_route_is_inter_pod_whitelisted():
    """Fix H2 : /journal/livrables doit etre dans _AGENT_INTER_POD_ROUTES."""
    assert "/journal/livrables" in _AGENT_INTER_POD_ROUTES, (
        "Route /journal/livrables absente des routes inter-pod -- H2 non fixe."
    )


def test_mark_published_rejects_phishing_url():
    """Fix H2 : published_url pointant hors domaines legitimes -> 400."""
    lid = _make_livrable()
    client = TestClient(app)
    r = client.post(
        f"/journal/livrables/{lid}/mark_published",
        json={"published_url": "https://evil.example.com/phishing"},
        headers={
            "User-Agent": "kube-probe/hub",
            "Authorization": f"Bearer {os.environ['HUB_API_KEY']}",
        },
    )
    assert r.status_code == 400
    assert "domaine legitime" in r.json().get("detail", "").lower()


def test_mark_published_accepts_sspcloud_url():
    """Fix H2 : *.user.lab.sspcloud.fr -> accepte."""
    lid = _make_livrable()
    client = TestClient(app)
    r = client.post(
        f"/journal/livrables/{lid}/mark_published",
        json={
            "published_url": (
                "https://user-nicolaslaval-qgis-mcp-bridge.user.lab.sspcloud.fr"
                "/published/abc123"
            ),
        },
        headers={
            "User-Agent": "kube-probe/hub",
            "Authorization": f"Bearer {os.environ['HUB_API_KEY']}",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["livrable_id"] == lid


def test_mark_published_accepts_gouv_fr():
    """Fix H2 : *.gouv.fr -> accepte."""
    lid = _make_livrable()
    client = TestClient(app)
    r = client.post(
        f"/journal/livrables/{lid}/mark_published",
        json={"published_url": "https://data.gouv.fr/livrables/abc"},
        headers={
            "User-Agent": "kube-probe/hub",
            "Authorization": f"Bearer {os.environ['HUB_API_KEY']}",
        },
    )
    assert r.status_code == 200


def test_mark_published_accepts_cerema_fr():
    """Fix H2 : *.cerema.fr -> accepte."""
    lid = _make_livrable()
    client = TestClient(app)
    r = client.post(
        f"/journal/livrables/{lid}/mark_published",
        json={"published_url": "https://livrables.cerema.fr/xyz"},
        headers={
            "User-Agent": "kube-probe/hub",
            "Authorization": f"Bearer {os.environ['HUB_API_KEY']}",
        },
    )
    assert r.status_code == 200


def test_mark_published_rejects_http_scheme():
    """Fix H2 : http:// (non-TLS) -> refuse (regex exige https://)."""
    lid = _make_livrable()
    client = TestClient(app)
    r = client.post(
        f"/journal/livrables/{lid}/mark_published",
        json={"published_url": "http://user-x.user.lab.sspcloud.fr/livrable"},
        headers={
            "User-Agent": "kube-probe/hub",
            "Authorization": f"Bearer {os.environ['HUB_API_KEY']}",
        },
    )
    assert r.status_code == 400


def test_mark_published_rejects_empty_url():
    """Fix H2 : published_url vide/absent -> 400."""
    lid = _make_livrable()
    client = TestClient(app)
    r = client.post(
        f"/journal/livrables/{lid}/mark_published",
        json={},
        headers={
            "User-Agent": "kube-probe/hub",
            "Authorization": f"Bearer {os.environ['HUB_API_KEY']}",
        },
    )
    assert r.status_code == 400
