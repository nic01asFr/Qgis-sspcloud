"""Le bureau et le chat qu'il embarque ne s'incrustent pas dans une page tierce.

Et le bureau ne parle qu'au chat qu'il embarque, servi en meme origine par le
proxy /agent/.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import main as hub_main  # noqa: E402

_SONDE = {"user-agent": "kube-probe/1.0"}  # contourne l'OIDC, cf. Bug #17
_DESK = (_ROOT / "templates" / "desk.html").read_text(encoding="utf-8")
_CSP = "frame-ancestors 'self'"


def test_le_bureau_interdit_l_incrustation_par_un_tiers() -> None:
    with TestClient(hub_main.app) as c:
        r = c.get("/desk", headers=_SONDE)
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-security-policy"] == _CSP


def test_le_proxy_relaie_la_politique_du_chat(monkeypatch) -> None:
    """La regle est posee par l'agent : le proxy ne doit pas la perdre."""
    class _Flux(httpx.AsyncByteStream):
        # Un corps en flux, comme celui de l'agent : le proxy le relit par
        # morceaux. Un corps donne d'un bloc serait deja consomme.
        async def __aiter__(self):
            yield b"<html><head></head><body>chat</body></html>"

    def _agent(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={
            "content-type": "text/html; charset=utf-8",
            "content-security-policy": _CSP,
        }, stream=_Flux())

    client_reel = httpx.AsyncClient

    class _ClientVersAgent(client_reel):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(_agent)
            super().__init__(*a, **k)

    monkeypatch.setattr(hub_main.httpx, "AsyncClient", _ClientVersAgent)
    monkeypatch.setenv("HUB_API_KEY", "cle-essai-proxy")

    r = TestClient(hub_main.app).get("/agent/?embed=1", headers=_SONDE)

    assert r.status_code == 200
    assert r.headers["content-security-policy"] == _CSP
    assert "chat" in r.text


def test_le_bureau_n_ecoute_que_sa_propre_origine() -> None:
    ecouteur = _DESK.split("if (d.type === 'qgis_publish_done'")[0].rsplit(
        "window.addEventListener('message'", 1)[1]
    assert "if (e.origin !== location.origin) return;" in ecouteur


def test_le_bureau_n_ecrit_qu_a_sa_propre_origine() -> None:
    assert "}, '*');" not in _DESK
    assert _DESK.count("}, location.origin);") >= 2


def test_un_tour_termine_rafraichit_le_panneau_des_couches() -> None:
    """Le panneau de gauche montrait l'etat d'avant le tour."""
    ecouteur = _DESK.split("if (e.origin !== location.origin) return;")[1][:900]
    bloc = ecouteur.split("if (d.type === 'qgis_agent_turn_done')")[1][:120]
    assert "refreshSourcesSummaries()" in bloc
