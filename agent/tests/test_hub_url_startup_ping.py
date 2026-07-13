"""Tests P1-4 : ping HUB_URL au startup pour detecter env vars fantomes.

Bug detecte en validation Sprint V0.3 : HUB_URL pointait vers un ingress
inexistant, le fetch briques echouait a 404 silencieusement, l'user voyait
un livrable sans briques. Ce ping proactif catch le probleme au boot du pod.
"""
import asyncio
import logging
import os
import sys
import types

# Stub sqlite_vec (dep optionnelle en local -- CI l'a via requirements).
if "sqlite_vec" not in sys.modules:
    _stub = types.ModuleType("sqlite_vec")
    _stub.load = lambda *a, **kw: None
    sys.modules["sqlite_vec"] = _stub

import httpx
import pytest


class _StubResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _StubClient:
    def __init__(self, status_code: int = 200, exc: Exception | None = None):
        self._status = status_code
        self._exc = exc
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, headers: dict | None = None):
        self.calls.append(url)
        if self._exc:
            raise self._exc
        return _StubResponse(self._status)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_ping_ok_200(monkeypatch, caplog):
    """Hub repond 200 -> log INFO 'ping OK'."""
    from agent import main as agent_main

    stub = _StubClient(status_code=200)
    monkeypatch.setattr(agent_main, "_HUB_URL", "https://hub.example.com")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: stub)

    with caplog.at_level(logging.INFO):
        _run(agent_main._hub_url_startup_ping())

    assert stub.calls == ["https://hub.example.com/"]
    assert any("ping OK" in r.message and "200" in r.message for r in caplog.records)


def test_ping_401_ok_middleware_oidc(monkeypatch, caplog):
    """Hub repond 401 (middleware OIDC) -> log INFO ping OK (< 500)."""
    from agent import main as agent_main

    stub = _StubClient(status_code=401)
    monkeypatch.setattr(agent_main, "_HUB_URL", "https://hub.example.com")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: stub)

    with caplog.at_level(logging.INFO):
        _run(agent_main._hub_url_startup_ping())

    # 401 est considere comme OK (le hub existe et repond)
    assert any("ping OK" in r.message for r in caplog.records)
    assert not any("inatteignable" in r.message for r in caplog.records)


def test_ping_connect_error_fantome(monkeypatch, caplog):
    """Hub ingress fantome (ConnectError) -> log WARNING explicite."""
    from agent import main as agent_main

    stub = _StubClient(exc=httpx.ConnectError("Name or service not known"))
    monkeypatch.setattr(agent_main, "_HUB_URL", "https://ghost-hub.example.com")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: stub)

    with caplog.at_level(logging.WARNING):
        _run(agent_main._hub_url_startup_ping())

    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("inatteignable" in m and "ghost-hub" in m for m in warnings)
    assert any("env var fantome" in m for m in warnings)


def test_ping_timeout(monkeypatch, caplog):
    """Hub cold-start / ingress lent -> log WARNING timeout."""
    from agent import main as agent_main

    stub = _StubClient(exc=httpx.TimeoutException("timeout after 5s"))
    monkeypatch.setattr(agent_main, "_HUB_URL", "https://slow-hub.example.com")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: stub)

    with caplog.at_level(logging.WARNING):
        _run(agent_main._hub_url_startup_ping())

    assert any("timeout" in r.message.lower() for r in caplog.records)


def test_ping_5xx_hub_broken(monkeypatch, caplog):
    """Hub repond 500 -> log WARNING (existe mais casse)."""
    from agent import main as agent_main

    stub = _StubClient(status_code=503)
    monkeypatch.setattr(agent_main, "_HUB_URL", "https://broken-hub.example.com")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: stub)

    with caplog.at_level(logging.WARNING):
        _run(agent_main._hub_url_startup_ping())

    assert any("503" in r.message and "erreur" in r.message.lower() for r in caplog.records)


def test_ping_never_raises(monkeypatch, caplog):
    """Fail-soft absolu : meme sur exception inconnue, le ping n'explose pas."""
    from agent import main as agent_main

    stub = _StubClient(exc=RuntimeError("unexpected"))
    monkeypatch.setattr(agent_main, "_HUB_URL", "https://x.example.com")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: stub)

    # Ne doit pas raise
    with caplog.at_level(logging.WARNING):
        _run(agent_main._hub_url_startup_ping())

    assert any("erreur inattendue" in r.message for r in caplog.records)
