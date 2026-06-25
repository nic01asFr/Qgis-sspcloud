"""Non-régression : le middleware OIDC laisse passer les endpoints OAuth/MCP
sans cookie navigateur (fix 2026-06-25).

Contexte : claude.ai (connecteur MCP distant) fait la découverte OAuth et
l'échange code->token COTE SERVEUR, sans cookie. Avant le fix, le middleware
`oidc_auth_middleware` renvoyait 401 "Auth requise" sur ces routes, ce qui
empêchait l'ajout du connecteur. Ce test verrouille la whitelist et garantit
que les routes RGPD-sensibles (/desk, /studies...) restent gatées.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import auth  # noqa: E402


class _FakeURL:
    def __init__(self, path: str):
        self.path = path

    def __str__(self) -> str:
        return f"https://user-test-qgis.user.lab.sspcloud.fr{self.path}"


class _FakeRequest:
    """Request minimal suffisant pour le chemin public du middleware."""

    def __init__(self, path: str, headers=None, cookies=None):
        self.url = _FakeURL(path)
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.state = type("S", (), {})()


_SENTINEL = object()


async def _call_next(_request):
    return _SENTINEL


def _run(path, headers=None, cookies=None):
    req = _FakeRequest(path, headers=headers, cookies=cookies)
    return asyncio.run(auth.oidc_auth_middleware(req, _call_next))


# ── Routes OAuth / découverte : doivent PASSER sans cookie ─────────────────────

def test_wellknown_auth_server_public():
    assert _run("/.well-known/oauth-authorization-server") is _SENTINEL


def test_wellknown_protected_resource_public():
    assert _run("/.well-known/oauth-protected-resource") is _SENTINEL


def test_oauth_token_public():
    assert _run("/oauth/token") is _SENTINEL


def test_authorize_public():
    assert _run("/authorize") is _SENTINEL


def test_authorize_confirm_public_via_startswith():
    # /authorize/confirm doit être couvert par le check startswith("/authorize/")
    assert _run("/authorize/confirm") is _SENTINEL


# ── Routes RGPD-sensibles : doivent rester GATÉES (pas de passthrough) ─────────

def test_desk_still_gated_json():
    # XHR sans cookie -> 401 JSON (pas de passthrough vers call_next)
    resp = _run("/desk", headers={"accept": "application/json"})
    assert resp is not _SENTINEL
    assert getattr(resp, "status_code", None) == 401


def test_desk_still_gated_redirect_html():
    # Navigation navigateur sans cookie -> redirect 302 portail
    resp = _run("/desk", headers={"accept": "text/html"})
    assert resp is not _SENTINEL
    assert getattr(resp, "status_code", None) == 302
