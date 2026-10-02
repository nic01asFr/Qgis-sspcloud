"""Registre des traitements en arriere-plan et leur surveillance (spec 2026-10-02).

Le registre est une vraie base SQLite dans un dossier temporaire ; le
workspace (poll_job, cancel_job, execute_async) et l'agent (avis de fin) sont
simules. Aucun reseau.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import auth  # noqa: E402
from hub import main as hub_main  # noqa: E402
from hub import taches_fond as tf  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def registre(tmp_path):
    return tf.Registre(tmp_path / "taches.db")


def _nouvelle(registre, **extra):
    donnees = {"id": "tf-1", "job_id": "t-1", "outil": "execute_python",
               "arguments": {"code": "x = 1"}, "libelle": "Calcul dans QGIS",
               "session_id": "conv-1", "sid": "etude-1", "mode": "arriere_plan",
               "statut": tf.EN_COURS}
    donnees.update(extra)
    return _run(registre.creer("nicolas", donnees))


def _sonde(etat, contenu=None):
    async def _s(tache):
        return {"etat": etat, "contenu": contenu or []}
    return _s


class _Avis:
    def __init__(self, reponse=True):
        self.recus = []
        self.reponse = reponse

    async def __call__(self, tache):
        self.recus.append(tache["id"])
        return self.reponse


# ── Stockage ────────────────────────────────────────────────────────────────

def test_le_registre_survit_au_redemarrage_du_hub(registre, tmp_path):
    _nouvelle(registre)
    autre = tf.Registre(tmp_path / "taches.db")  # nouveau processus
    tache = _run(autre.lire("tf-1"))
    assert tache["libelle"] == "Calcul dans QGIS"
    assert tache["arguments"] == {"code": "x = 1"}
    assert tache["statut"] == tf.EN_COURS


def test_le_registre_est_par_utilisateur(registre):
    _nouvelle(registre)
    assert _run(registre.lire("tf-1", "nicolas")) is not None
    assert _run(registre.lire("tf-1", "autre")) is None
    assert _run(registre.lister("autre")) == []


def test_l_agent_ne_modifie_que_les_champs_permis(registre):
    _nouvelle(registre)
    doc = _run(registre.maj("tf-1", {"username": "pirate", "battement_at": 0,
                                     "statut": "n_importe_quoi", "mode": "tour",
                                     "vu_at": 42.0}))
    assert doc["username"] == "nicolas" and doc["battement_at"] != 0
    assert doc["statut"] == tf.EN_COURS
    assert doc["mode"] == "tour" and doc["vu_at"] == 42.0


def test_filtres_de_la_liste(registre):
    _nouvelle(registre)
    _nouvelle(registre, id="tf-2", session_id="conv-2", sid="etude-2",
              statut=tf.TERMINEE)
    assert [t["id"] for t in _run(registre.lister("nicolas", actives=True))] == ["tf-1"]
    assert [t["id"] for t in _run(registre.lister("nicolas", session_id="conv-2"))] == ["tf-2"]
    assert [t["id"] for t in _run(registre.lister("nicolas", sid="etude-1"))] == ["tf-1"]


def test_des_arguments_trop_gros_ne_sont_pas_relancables(registre):
    doc = _nouvelle(registre, arguments={"code": "x" * 200_000})
    assert doc["relancable"] is False
    assert len(json.dumps(doc["arguments"])) < 3000


# ── Surveillance ────────────────────────────────────────────────────────────

def test_une_tache_qui_tourne_reste_en_cours_et_bat(registre):
    _nouvelle(registre)
    avis = _Avis()
    _run(tf.tour_de_surveillance(registre, _sonde(
        {"status": "running", "heartbeat_age_s": 1.0, "stage": "running"}), avis,
        maintenant=10_000))
    t = _run(registre.lire("tf-1"))
    assert t["statut"] == tf.EN_COURS and t["battement_at"] == 10_000
    assert t["etat_qgis"]["heartbeat_age_s"] == 1.0
    assert avis.recus == []


def test_qgis_fige_pendant_un_script_long_n_est_pas_une_perte(registre):
    _nouvelle(registre)
    _run(tf.tour_de_surveillance(registre, _sonde(
        {"status": "qt_frozen", "heartbeat_age_s": 2.0}), _Avis()))
    assert _run(registre.lire("tf-1"))["statut"] == tf.EN_COURS


def test_une_tache_finie_est_signalee_a_l_agent(registre):
    _nouvelle(registre)
    avis = _Avis()
    contenu = [{"type": "text", "text": '{"verification": {"count": 4812}}'},
               {"type": "image", "data": "QUJD"}]
    bilan = _run(tf.tour_de_surveillance(registre, _sonde({"status": "done"}, contenu), avis))
    t = _run(registre.lire("tf-1"))
    assert t["statut"] == tf.TERMINEE and t["fini_at"]
    assert t["resultat"] == '{"verification": {"count": 4812}}'
    assert avis.recus == ["tf-1"] and bilan["avisees"] == 1


def test_une_tache_rattachee_n_est_plus_signalee(registre):
    _nouvelle(registre, statut=tf.TERMINEE)
    _run(registre.maj("tf-1", {"rattachee": True}))
    avis = _Avis()
    _run(tf.tour_de_surveillance(registre, _sonde({}), avis))
    assert avis.recus == []


def test_un_avis_refuse_est_retente_puis_abandonne(registre):
    _nouvelle(registre, statut=tf.TERMINEE)
    avis = _Avis(reponse=False)
    for _ in range(tf.MAX_AVIS + 3):
        _run(tf.tour_de_surveillance(registre, _sonde({}), avis))
    assert len(avis.recus) == tf.MAX_AVIS


def test_une_tache_suivie_par_son_tour_n_est_pas_conclue_par_le_hub(registre):
    _nouvelle(registre, mode="tour")
    avis = _Avis()
    _run(tf.tour_de_surveillance(registre, _sonde({"status": "done"}), avis))
    t = _run(registre.lire("tf-1"))
    assert t["statut"] == tf.EN_COURS and t["mode"] == "tour"
    assert avis.recus == []


def test_un_tour_disparu_fait_passer_la_tache_en_arriere_plan(registre):
    _nouvelle(registre, mode="tour")
    _run(registre.maj("tf-1", {"vu_at": 0.0}))
    _run(tf.tour_de_surveillance(registre, _sonde({"status": "running"}), _Avis(),
                                 maintenant=1_000))
    assert _run(registre.lire("tf-1"))["mode"] == "arriere_plan"


def test_un_calcul_inconnu_du_workspace_est_interrompu(registre):
    _nouvelle(registre)
    avis = _Avis()
    _run(tf.tour_de_surveillance(registre, _sonde({"error": "Unknown job_id: t-1"}), avis))
    t = _run(registre.lire("tf-1"))
    assert t["statut"] == tf.INTERROMPUE
    assert "redémarrage" in t["raison"]
    assert avis.recus == ["tf-1"]  # le message d'interruption rejoint la conversation


def test_un_battement_arrete_interrompt(registre):
    _nouvelle(registre)
    _run(tf.tour_de_surveillance(registre, _sonde(
        {"status": "running", "heartbeat_age_s": tf.BATTEMENT_PERDU_S + 1}), _Avis()))
    assert _run(registre.lire("tf-1"))["statut"] == tf.INTERROMPUE


def test_un_workspace_muet_n_interrompt_qu_apres_le_delai(registre):
    doc = _nouvelle(registre)

    async def _muet(tache):
        return {"injoignable": True}

    _run(tf.tour_de_surveillance(registre, _muet, _Avis(), maintenant=doc["battement_at"] + 10))
    assert _run(registre.lire("tf-1"))["statut"] == tf.EN_COURS
    _run(tf.tour_de_surveillance(registre, _muet, _Avis(),
                                 maintenant=doc["battement_at"] + tf.INJOIGNABLE_PERDU_S + 1))
    t = _run(registre.lire("tf-1"))
    assert t["statut"] == tf.INTERROMPUE and "ne répond plus" in t["raison"]


def test_une_sonde_qui_casse_ne_casse_pas_la_surveillance(registre):
    _nouvelle(registre)
    _nouvelle(registre, id="tf-2", job_id="t-2")

    async def _casse(tache):
        if tache["id"] == "tf-1":
            raise RuntimeError("boum")
        return {"etat": {"status": "done"}, "contenu": []}

    _run(tf.tour_de_surveillance(registre, _casse, _Avis()))
    assert _run(registre.lire("tf-2"))["statut"] == tf.TERMINEE


def test_un_calcul_annule_mais_deja_commence_finit_annule(registre):
    _nouvelle(registre)
    _run(registre.maj("tf-1", {"annulation_demandee": True}))
    _run(tf.tour_de_surveillance(registre, _sonde({"status": "done"}), _Avis()))
    assert _run(registre.lire("tf-1"))["statut"] == tf.ANNULEE


# ── Actions de l'utilisateur ────────────────────────────────────────────────

def test_annuler_une_tache_en_attente(registre):
    tache = _nouvelle(registre, statut=tf.EN_ATTENTE)

    async def _annuler(t):
        return {"success": True, "status": "cancelled"}

    doc = _run(tf.annuler(registre, tache, _annuler))
    assert doc["statut"] == tf.ANNULEE and doc["rattachee"] is True


def test_annuler_une_tache_commencee_la_laisse_occuper_qgis(registre):
    tache = _nouvelle(registre)

    async def _annuler(t):
        return {"success": False, "status": "already_dispatched_cannot_cancel"}

    doc = _run(tf.annuler(registre, tache, _annuler))
    assert doc["statut"] == tf.EN_COURS and doc["annulation_demandee"] is True


def test_abandonner_une_tache_interrompue(registre):
    tache = _nouvelle(registre, statut=tf.INTERROMPUE)

    async def _jamais(t):
        raise AssertionError("rien a annuler dans QGIS")

    assert _run(tf.annuler(registre, tache, _jamais))["statut"] == tf.ANNULEE


def test_relancer_une_tache_interrompue(registre):
    tache = _nouvelle(registre, statut=tf.INTERROMPUE, client_id="conv-1:a:b")
    _run(registre.maj("tf-1", {"rattachee": True, "message": "interrompu"}))
    tache = _run(registre.lire("tf-1"))
    soumis = []

    async def _soumettre(t, client_id):
        soumis.append((t["outil"], t["arguments"], client_id))
        return {"job_id": "t-2", "status": "queued"}

    doc = _run(tf.relancer(registre, tache, _soumettre))
    assert soumis == [("execute_python", {"code": "x = 1"}, "conv-1:a:b:relance-2")]
    assert doc["job_id"] == "t-2" and doc["statut"] == tf.EN_ATTENTE
    assert doc["mode"] == "arriere_plan" and doc["rattachee"] is False
    assert doc["tentatives"] == 2


def test_une_tache_terminee_ne_se_relance_pas(registre):
    tache = _nouvelle(registre, statut=tf.TERMINEE)

    async def _soumettre(t, c):
        raise AssertionError("pas de soumission")

    with pytest.raises(ValueError):
        _run(tf.relancer(registre, tache, _soumettre))


# ── API du hub ──────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    utilisateur = {"username": "nicolas", "scope": None}

    async def _utilisateur():
        return utilisateur

    monkeypatch.setattr(hub_main, "_REGISTRE_TACHES", tf.Registre(tmp_path / "t.db"))
    monkeypatch.setattr(hub_main, "_ONYXIA_USER", "nicolas")
    hub_main.app.dependency_overrides[auth.get_current_user] = _utilisateur
    c = TestClient(hub_main.app, headers={"user-agent": "kube-probe/1.0"})
    c.utilisateur = utilisateur
    yield c
    hub_main.app.dependency_overrides.pop(auth.get_current_user, None)


def _creer(client, **extra):
    corps = {"id": "tf-1", "job_id": "t-1", "outil": "execute_python",
             "arguments": {"code": "x"}, "libelle": "Calcul dans QGIS",
             "session_id": "conv-1", "sid": "etude-1", "mode": "tour"}
    corps.update(extra)
    return client.post("/taches", json=corps)


def test_api_creer_lister_lire_mettre_a_jour(client):
    r = _creer(client)
    assert r.status_code == 201 and r.json()["statut"] == tf.EN_ATTENTE
    assert "arguments" not in r.json()
    liste = client.get("/taches", params={"actives": "1"}).json()
    assert liste["actives"] == 1 and liste["taches"][0]["id"] == "tf-1"
    assert client.get("/taches", params={"session_id": "autre"}).json()["taches"] == []
    r = client.patch("/taches/tf-1", json={"statut": "terminee", "rattachee": True,
                                            "resultat": "ok"})
    assert r.json()["statut"] == "terminee" and r.json()["fini_at"]
    assert client.get("/taches/tf-1").json()["resultat"] == "ok"
    assert client.get("/taches/inconnue").status_code == 404


def test_api_le_bureau_lit_les_memes_taches(client):
    _creer(client)
    d = client.get("/desk/taches").json()
    assert d["actives"] == 1 and d["taches"][0]["libelle"] == "Calcul dans QGIS"


def test_api_annuler_et_relancer(client, monkeypatch):
    _creer(client, statut=tf.EN_ATTENTE)

    async def _annuler(t):
        return {"success": True, "status": "cancelled"}

    async def _soumettre(t, client_id):
        return {"job_id": "t-9"}

    monkeypatch.setattr(hub_main, "_annuler_job_tache", _annuler)
    monkeypatch.setattr(hub_main, "_soumettre_tache", _soumettre)
    assert client.post("/taches/tf-1/annuler").json()["statut"] == tf.ANNULEE
    # Une tache annulee ne se relance pas ; une interrompue, si.
    assert client.post("/taches/tf-1/relancer").status_code == 409
    _creer(client, id="tf-2", statut=tf.INTERROMPUE)
    r = client.post("/desk/taches/tf-2/relancer")
    assert r.status_code == 200 and r.json()["job_id"] == "t-9"


def test_api_une_cle_restreinte_n_a_pas_acces_au_registre(client):
    client.utilisateur["scope"] = {"mode": "agent", "tools": ["get_project_info"]}
    assert client.get("/taches").status_code == 403
    assert _creer(client).status_code == 403


def test_les_routes_sont_joignables_en_inter_pod():
    assert "/taches" in auth._OIDC_MIDDLEWARE_INTER_POD


# ── Le detour execute_async ne contourne pas le scope ───────────────────────

def test_execute_async_d_un_outil_hors_scope_est_refuse():
    obj = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "execute_async",
                      "arguments": {"tool": "run_recipe", "arguments": {}}}}
    refus = hub_main._tool_call_denied(obj, ["execute_async", "poll_job"])
    assert refus is not None and b"run_recipe" in refus.body
    obj["params"]["arguments"]["tool"] = "poll_job"
    assert hub_main._tool_call_denied(obj, ["execute_async", "poll_job"]) is None


# ── Bureau : pastille, liste, notification ──────────────────────────────────

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8")
_SCRIPT = _DESK.split('<script id="taches-fond-bureau">')[1].split("</script>")[0]


def test_la_pastille_est_dans_la_barre_d_etat():
    barre = _DESK.split('<footer class="desk-status">')[1].split("</footer>")[0]
    assert 'id="taches-pastille"' in barre
    assert 'aria-controls="taches-panneau"' in barre
    assert 'aria-expanded="false"' in barre


def test_la_pastille_dit_combien_de_calculs_tournent():
    assert "' calcul' + (n > 1 ? 's' : '') + ' en cours'" in _SCRIPT
    assert "fetch('/desk/taches?limite=20'" in _SCRIPT


def test_la_fin_est_notifiee_discretement_et_annoncee():
    assert 'id="taches-annonce" class="taches-annonce" role="status" aria-live="polite"' in _DESK
    assert "Calcul terminé : « " in _SCRIPT
    assert "Le résultat est dans la conversation." in _SCRIPT


def test_le_panneau_se_ferme_au_clavier():
    assert "e.key === 'Escape'" in _SCRIPT
    assert "pastille.focus()" in _SCRIPT
    assert "'Relancer'" in _SCRIPT and "'Abandonner'" in _SCRIPT


def test_le_bureau_ecoute_le_chat_de_meme_origine():
    assert "e.origin !== location.origin" in _SCRIPT
    assert "d.type === 'qgis_taches_maj'" in _SCRIPT
