"""Tests fix H1 revue adversariale Sprint V0.4.1 : le bypass UA kube-probe
doit refuser les requetes qui portent un header X-Forwarded-For.

Un vrai kube-probe (kubelet -> pod) est un hop direct : pas d'ingress dans
le chemin, donc pas de header X-Forwarded-For. Un attaquant Internet qui
forge l'UA passe forcement par l'ingress SSPCloud qui ajoute ce header.

Avant fix : `curl -H "User-Agent: kube-probe" https://.../desk` depuis
Internet bypass toute l'auth OIDC.

Apres fix : rejete avec 401 (redirect portail).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import main as hub_main  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(hub_main.app)


def test_kube_probe_without_xff_bypasses_oidc(client):
    """Vrai kube-probe (pas de X-Forwarded-For) -> pas d'exigence auth."""
    r = client.get("/health", headers={"User-Agent": "kube-probe/1.28"})
    # /health est deja route publique. Test complementaire : /desk avec probe
    # ne redirige plus vers portail (401/302) car UA passe.
    r = client.get(
        "/desk",
        headers={"User-Agent": "kube-probe/1.28"},
        follow_redirects=False,
    )
    # Pas de redirect vers portail -> UA a passe le bypass.
    # Reponse peut etre 200 (page desk) ou 500 (deps manquants en test).
    # L'essentiel : pas de 302 vers portail.
    if r.status_code == 302:
        assert "portal" not in r.headers.get("location", "").lower(), (
            "kube-probe SANS XFF devrait bypass OIDC, pas rediriger vers portail"
        )


def test_kube_probe_with_xff_is_rejected(client):
    """Fix H1 : kube-probe UA + X-Forwarded-For -> auth exigee.

    Simule un attaquant Internet qui forge le UA. L'ingress SSPCloud ajoute
    forcement X-Forwarded-For -> bypass refuse.
    """
    r = client.get(
        "/desk",
        headers={
            "User-Agent": "kube-probe/1.28",
            "X-Forwarded-For": "203.0.113.42",  # IP publique arbitraire
        },
        follow_redirects=False,
    )
    # Requiert auth -> 302 redirect portail (UI HTML) ou 401 JSON.
    assert r.status_code in (302, 401), (
        f"Attendu 302/401 (auth exigee), obtenu {r.status_code}. "
        "Le bypass UA kube-probe ne verifie pas X-Forwarded-For -> H1 non fixe."
    )


def test_kube_probe_with_xff_inter_pod_route_still_needs_bearer(client):
    """Route inter-pod avec kube-probe + XFF forge : le bypass UA ne passe
    plus, on retombe sur le check Bearer HUB_API_KEY qui exige la vraie cle.
    """
    r = client.get(
        "/briques",
        headers={
            "User-Agent": "kube-probe/1.28",
            "X-Forwarded-For": "203.0.113.42",
            "Authorization": "Bearer wrong-key",
        },
    )
    # Sans bonne cle : 401 (inter-pod echec) puis fallback OIDC (pas de cookie)
    # -> 401 JSON ou 302 redirect.
    assert r.status_code in (302, 401), (
        f"Attendu rejet auth, obtenu {r.status_code}"
    )
