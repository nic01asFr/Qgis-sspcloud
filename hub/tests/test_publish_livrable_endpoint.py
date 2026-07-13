"""Tests de l'endpoint POST /api/livrable/publish (chantier G4-b-3d).

Couvre :
  1. POST payload valide -> 200 + published_url renvoye par _publish_from_hint
  2. POST sans livrable_id -> 400 (validation Pydantic)
  3. Agent journal down (httpx raise) -> 200 fail-soft, journal_synced=False
  4. Auth Depends -> 401 sans user (dependency_overrides retire)
  5. publish_hint sans sid/aid -> 400 propage depuis _publish_from_hint
  6. _publish_from_hint raise Exception -> 500 avec log warn

L'endpoint reel _publish_from_hint appelle publish_assembly_endpoint qui
touche PVC + S3 + DB. Ici on le mocke via monkeypatch : la logique reelle
est deja couverte par test_sprint10_wave1_publish.py + tests assemblies.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import auth  # noqa: E402
from hub import main as hub_main  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def client_authed():
    """TestClient avec auth.get_current_user override -> user test stub."""
    async def _fake_user():
        return {"username": "test", "scope": "user"}

    hub_main.app.dependency_overrides[auth.get_current_user] = _fake_user
    with TestClient(hub_main.app) as c:
        yield c
    hub_main.app.dependency_overrides.pop(auth.get_current_user, None)


@pytest.fixture
def client_no_auth():
    """TestClient SANS override : get_current_user reel -> 401 sans creds."""
    hub_main.app.dependency_overrides.pop(auth.get_current_user, None)
    with TestClient(hub_main.app) as c:
        yield c


def _headers():
    """User-Agent kube-probe : court-circuite middleware OIDC (cf.
    hub/hub/auth.py oidc_auth_middleware etape 2). Auth Depends reste
    en vigueur.
    """
    return {"user-agent": "kube-probe/1.0"}


# ── Tests ───────────────────────────────────────────────────────────────────


def test_publish_valid_payload_returns_200_and_url(client_authed, monkeypatch):
    """POST valide -> 200 + published_url + journal_synced=True."""
    async def _fake_publish(publish_hint, username):
        assert publish_hint == {"sid": "s1", "aid": "a1"}
        assert username == "test"
        return "https://hub.example/published/test/assembly/assembly-a1"

    monkeypatch.setattr(hub_main, "_publish_from_hint", _fake_publish)

    # Mock AGENT_URL + httpx pour observer notif agent
    monkeypatch.setattr(hub_main, "_AGENT_URL", "http://agent-svc:8100")

    class _FakeResp:
        def __init__(self, status_code=200):
            self.status_code = status_code

    class _FakeClient:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, **_kw):
            assert "/journal/livrables/lv-123/mark_published" in url
            return _FakeResp(200)

    monkeypatch.setattr(hub_main.httpx, "AsyncClient", _FakeClient)

    resp = client_authed.post(
        "/api/livrable/publish",
        json={
            "livrable_id": "lv-123",
            "publish_hint": {"sid": "s1", "aid": "a1"},
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["published_url"].startswith("https://hub.example/")
    assert body["livrable_id"] == "lv-123"
    assert body["journal_synced"] is True


def test_publish_missing_livrable_id_returns_400(client_authed, monkeypatch):
    """Body sans livrable_id -> 400 (Pydantic ValidationError)."""
    async def _fake_publish(*_a, **_kw):
        raise AssertionError("_publish_from_hint ne doit pas etre appele")

    monkeypatch.setattr(hub_main, "_publish_from_hint", _fake_publish)

    resp = client_authed.post(
        "/api/livrable/publish",
        json={"publish_hint": {"sid": "s1", "aid": "a1"}},
        headers=_headers(),
    )
    assert resp.status_code == 400, resp.text
    detail = str(resp.json().get("detail", "")).lower()
    assert "livrable_id" in detail


def test_publish_empty_livrable_id_returns_400(client_authed, monkeypatch):
    """livrable_id vide (str vide ou espaces) -> 400 explicite."""
    async def _fake_publish(*_a, **_kw):
        raise AssertionError("_publish_from_hint ne doit pas etre appele")

    monkeypatch.setattr(hub_main, "_publish_from_hint", _fake_publish)

    resp = client_authed.post(
        "/api/livrable/publish",
        json={
            "livrable_id": "   ",
            "publish_hint": {"sid": "s1", "aid": "a1"},
        },
        headers=_headers(),
    )
    assert resp.status_code == 400
    assert "livrable_id" in str(resp.json().get("detail", "")).lower()


def test_publish_agent_down_returns_200_fail_soft(client_authed, monkeypatch):
    """Agent qui refuse la connexion (httpx.ConnectError) : le hub log
    warning mais renvoie 200 avec journal_synced=False. L'user a son URL.
    """
    async def _fake_publish(publish_hint, username):
        return "https://hub.example/published/test/assembly/assembly-a1"

    monkeypatch.setattr(hub_main, "_publish_from_hint", _fake_publish)
    monkeypatch.setattr(hub_main, "_AGENT_URL", "http://agent-svc:8100")

    class _FakeClient:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, *_a, **_kw):
            raise httpx.ConnectError("agent down")

    monkeypatch.setattr(hub_main.httpx, "AsyncClient", _FakeClient)

    resp = client_authed.post(
        "/api/livrable/publish",
        json={
            "livrable_id": "lv-456",
            "publish_hint": {"sid": "s1", "aid": "a1"},
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["published_url"].startswith("https://")
    assert body["journal_synced"] is False


def test_publish_requires_auth(client_no_auth):
    """Sans override auth et sans Bearer, HTTPBearer(auto_error=False) +
    get_current_user leve 401 Not authenticated.
    """
    resp = client_no_auth.post(
        "/api/livrable/publish",
        json={
            "livrable_id": "lv-000",
            "publish_hint": {"sid": "s1", "aid": "a1"},
        },
        headers=_headers(),
    )
    assert resp.status_code == 401, resp.text


def test_publish_hint_incomplete_returns_400(client_authed):
    """publish_hint sans sid ni aid/cid -> ValueError -> 400 propage.

    On n'override PAS _publish_from_hint : on veut tester le vrai path
    de validation. Comme aucun sid n'est fourni, _publish_from_hint leve
    ValueError avant tout I/O.
    """
    resp = client_authed.post(
        "/api/livrable/publish",
        json={
            "livrable_id": "lv-777",
            "publish_hint": {},
        },
        headers=_headers(),
    )
    assert resp.status_code == 400, resp.text
    detail = str(resp.json().get("detail", "")).lower()
    assert "sid" in detail or "publish_hint" in detail


def test_publish_from_hint_raises_generic_returns_500(
    client_authed, monkeypatch,
):
    """Une exception inattendue dans _publish_from_hint -> 500 (pas de crash
    silencieux ; log.error remonte le type d'exception).
    """
    async def _boom(*_a, **_kw):
        raise RuntimeError("S3 timeout")

    monkeypatch.setattr(hub_main, "_publish_from_hint", _boom)

    resp = client_authed.post(
        "/api/livrable/publish",
        json={
            "livrable_id": "lv-999",
            "publish_hint": {"sid": "s1", "aid": "a1"},
        },
        headers=_headers(),
    )
    assert resp.status_code == 500, resp.text


def test_publish_no_agent_url_configured(client_authed, monkeypatch):
    """AGENT_URL vide -> pas de tentative de sync, journal_synced=False mais
    pas d'erreur (deploiement standalone sans agent).
    """
    async def _fake_publish(publish_hint, username):
        return "https://hub.example/published/test/assembly/x"

    monkeypatch.setattr(hub_main, "_publish_from_hint", _fake_publish)
    monkeypatch.setattr(hub_main, "_AGENT_URL", "")

    # Si _AGENT_URL est falsy, httpx ne doit pas etre appele. On plante
    # AsyncClient pour verifier qu'on ne l'utilise pas.
    class _NeverCalled:
        def __init__(self, *_a, **_kw):
            raise AssertionError("httpx.AsyncClient ne doit pas etre construit")

    monkeypatch.setattr(hub_main.httpx, "AsyncClient", _NeverCalled)

    resp = client_authed.post(
        "/api/livrable/publish",
        json={
            "livrable_id": "lv-111",
            "publish_hint": {"sid": "s1", "aid": "a1"},
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["journal_synced"] is False


if __name__ == "__main__":
    print("Utiliser pytest pour executer les tests.")
