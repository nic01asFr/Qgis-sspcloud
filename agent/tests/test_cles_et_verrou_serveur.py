"""La cle d'un agent partage ne passe pas par le modele ; le verrou de profil
est decide par le serveur (audit securite des acces, 2026-09-26)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _executer(coro):
    """Execute sans toucher a la boucle courante du fil principal : d'autres
    tests de la suite s'appuient sur `asyncio.get_event_loop()`."""
    boucle = asyncio.new_event_loop()
    try:
        return boucle.run_until_complete(coro)
    finally:
        boucle.close()


import pytest  # noqa: E402

from agent import cles_deleguees  # noqa: E402
from agent import native_tools_v2 as nt  # noqa: E402

CLE = "qgisk_alice_" + "0123456789abcdef" * 2


@pytest.fixture(autouse=True)
def _coffre_vide():
    cles_deleguees.purger_tout()
    yield
    cles_deleguees.purger_tout()


def _hub_factice(monkeypatch, reponses):
    appels = []

    async def _hub_call(methode, chemin, json_body=None, **k):
        appels.append((methode, chemin, json_body))
        return reponses.get(methode, {})

    monkeypatch.setattr(nt, "_hub_call", _hub_call)
    return appels


def test_create_agent_ne_rend_pas_la_cle(monkeypatch):
    _hub_factice(monkeypatch, {"POST": {
        "key": CLE, "key_masked": CLE[:14] + "…", "sid": "s" * 12,
        "warning_copy_now": "Copiez-la maintenant."}})
    res = _executer(nt.create_agent("s" * 12))
    texte = json.dumps(res, ensure_ascii=False)
    assert CLE not in texte
    assert "key" not in res
    assert res["agent_ref"].startswith("agent-")
    assert res["key_masked"].startswith("qgisk_alice_")
    assert res["agent_ref"] in res["lien_remise_cle"]


def test_publish_et_revoke_resolvent_la_reference(monkeypatch):
    appels = _hub_factice(monkeypatch, {
        "POST": {"published": True,
                 "published_url": "https://hub/agent-share/abc"},
        "DELETE": {}})
    ref = cles_deleguees.deposer(CLE)
    res = _executer(nt.publish_agent("s" * 12, ref))
    assert appels[-1][1] == f"/studies/{'s' * 12}/scoped-keys/{CLE}/publish"
    # L'URL /agent-share n'existe pas : elle n'est pas donnee au modele.
    assert "published_url" not in res
    assert "published_url_notice" in res
    _executer(nt.revoke_agent("s" * 12, ref))
    assert appels[-1] == ("DELETE", f"/studies/{'s' * 12}/scoped-keys/{CLE}", None)


def test_reference_inconnue_refusee_sans_appel_hub(monkeypatch):
    appels = _hub_factice(monkeypatch, {})
    res = _executer(nt.publish_agent("s" * 12, "agent-inconnu"))
    assert res["error"] == "agent_ref_inconnue"
    assert appels == []


def test_remise_unique():
    ref = cles_deleguees.deposer(CLE)
    assert cles_deleguees.remettre(ref) == CLE
    assert cles_deleguees.remettre(ref) is None
    # La reference reste resolue pour publier / revoquer.
    assert cles_deleguees.resoudre(ref) == CLE


def test_remise_expiree(monkeypatch):
    ref = cles_deleguees.deposer(CLE)
    t0 = cles_deleguees.time.time()
    monkeypatch.setattr(cles_deleguees.time, "time", lambda: t0 + 7200)
    assert cles_deleguees.remettre(ref) is None


def test_route_de_remise(monkeypatch):
    from fastapi.testclient import TestClient
    from agent import main as agent_main

    ref = cles_deleguees.deposer(CLE)
    with TestClient(agent_main.app) as client:
        h = {"user-agent": "kube-probe/1.0"}
        r1 = client.get(f"/api/cles-deleguees/{ref}", headers=h)
        assert r1.status_code == 200
        assert r1.json()["key"] == CLE
        assert r1.headers["cache-control"] == "no-store"
        r2 = client.get(f"/api/cles-deleguees/{ref}", headers=h)
        assert r2.status_code == 404


@pytest.mark.parametrize("session, demande, profil, attendu", [
    # Bureau et session historique : jamais verrouilles.
    ("study:abcdefabcdef", True, "standard", (False, "standard")),
    ("chat_uuid_historique", True, "map_composer", (False, "map_composer")),
    # Editeur libre : verrou accorde, profil du formulaire.
    ("study:abcdefabcdef:draft:d1", True, "map_composer", (True, "map_composer")),
    # Assistance composant : verrou accorde, profil impose par le serveur.
    ("assist:abcdefabcdef:cid:c1", True, "standard", (True, "component_assist")),
    ("assist:abcdefabcdef:aid:a1", True, "standard", (True, "assembly_assist")),
    # Sans demande : rien ne change.
    ("assist:abcdefabcdef:cid:c1", False, "standard", (False, "standard")),
])
def test_verrou_decide_par_le_serveur(session, demande, profil, attendu):
    from agent import main as agent_main
    assert agent_main._verrou_de_profil(demande, session, profil) == attendu
