"""Proxy /agent : le shim fetch prefixe les appels mémoire."""

from __future__ import annotations

from pathlib import Path

_MAIN = (Path(__file__).resolve().parents[1] / "hub" / "main.py").read_text(
    encoding="utf-8"
)


def test_shim_fetch_prefixe_urls_absolues() -> None:
    assert 'var PREFIX = "/agent"' in _MAIN
    assert "!url.startsWith(\"/agent/\")" in _MAIN


def test_routes_desk_memory_relayent_agent() -> None:
    assert '@app.get("/desk/memory")' in _MAIN
    assert '"/user/memory"' in _MAIN.split("@app.get(\"/desk/memory\")")[1][:400]
