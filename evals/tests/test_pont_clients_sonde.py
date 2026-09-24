"""Pont, clients HTTP et sonde, avec des doublures : aucun appel reseau."""
from __future__ import annotations

import io
import json
import re
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from evals.clients import ClientAgent, ClientHub, ErreurHttp
from evals.pont import ErreurPont, PontCommande, PontHttp, executer_python
from evals.scenario import charger
from evals.sonde import inspections_requises, preparer, script_ajouter_couches, script_sonde, script_vider
from evals.tests.aide import FIXTURES

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


class Reponse(io.BytesIO):
    def __init__(self, contenu: bytes, status: int = 200):
        super().__init__(contenu)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Ouvreur:
    """Remplace urllib.request.urlopen et garde les requetes."""

    def __init__(self, reponses: list[bytes]):
        self.reponses = list(reponses)
        self.requetes = []

    def __call__(self, req, timeout=None):
        self.requetes.append(req)
        return Reponse(self.reponses.pop(0) if self.reponses else b"{}")


# ── Pont ───────────────────────────────────────────────────────────────────

def test_pont_http_corps_et_url():
    ouvreur = Ouvreur([b'{"success": true, "result": {"n": 1}}'])
    pont = PontHttp("http://localhost:8080", ouvrir=ouvreur)
    assert executer_python(pont, "result['n'] = 1") == {"n": 1}
    req = ouvreur.requetes[0]
    assert req.full_url == "http://localhost:8080/api/command"
    corps = json.loads(req.data)
    assert corps["action"] == "execute_python" and corps["params"] == {"code": "result['n'] = 1"}


def test_pont_commande_passe_la_requete_sur_stdin():
    vus = {}

    def executer(argv, input, capture_output, text, timeout):
        vus.update(argv=argv, input=json.loads(input))
        return subprocess.CompletedProcess(argv, 0, stdout='{"success": true, "result": {"ok": 1}}', stderr="")

    pont = PontCommande('kubectl exec -i -n user-x pod-ws -- curl -s --data-binary @- http://localhost:8080/api/command',
                        executer=executer)
    assert executer_python(pont, "x") == {"ok": 1}
    assert vus["argv"][:3] == ["kubectl", "exec", "-i"]
    assert vus["input"]["action"] == "execute_python"


def test_pont_commande_en_echec():
    def executer(argv, **k):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="error: pod introuvable")

    with pytest.raises(ErreurPont, match="pod introuvable"):
        PontCommande("kubectl exec x", executer=executer).commande("execute_python", {})


def test_execute_python_en_erreur():
    class Pont:
        def commande(self, action, params, timeout=120):
            return {"success": False, "error": "NameError: x"}

    with pytest.raises(ErreurPont, match="NameError"):
        executer_python(Pont(), "x")


def test_execute_python_resultat_enrobe():
    class Pont:
        def commande(self, action, params, timeout=120):
            return {"success": True, "result": {"result": {"etat": {}}}}

    assert executer_python(Pont(), "x") == {"etat": {}}


# ── Sonde ──────────────────────────────────────────────────────────────────

_ECRITURES = re.compile(
    r"removeMapLayer|addMapLayer|setProjectVariable|removeProjectVariable|startEditing|"
    r"commitChanges|deleteFeature|addFeature|\.write\(|\.save\(|writeAsVectorFormat|processing\.run"
)


def test_sonde_compile_et_ne_modifie_rien():
    code = script_sonde([{"motif": "b[aâ]ti", "contour_motif": "commune"}])
    compile(code, "<sonde>", "exec")
    assert not _ECRITURES.search(code)
    assert 'result["etat"]' in code


def test_scripts_de_preparation_compilent():
    compile(script_vider(), "<vider>", "exec")
    code = script_ajouter_couches([{"nom": "n'importe", "uri": "/data/x.gpkg|layername=a"}])
    compile(code, "<ajouter>", "exec")


def test_inspections_requises_depuis_le_scenario():
    s2 = next(s for s in charger([SCENARIOS]) if s.id == "S2-bati-aix-perimetre-communal")
    assert inspections_requises(s2) == [
        {"motif": "b[aâ]ti", "contour_motif": "commune|contour|limite|p[ée]rim[eè]tre"}]


def test_preparer_applique_les_preconditions_dans_l_ordre():
    s3 = next(s for s in charger([SCENARIOS]) if s.id == "S3-changement-de-zone")

    class Pont:
        def __init__(self):
            self.actions = []

        def commande(self, action, params, timeout=120):
            self.actions.append((action, params.get("target") or "script"))
            return {"success": True, "result": {"retirees": 4}}

    pont = Pont()
    journal = preparer(pont, s3)
    assert pont.actions == [("execute_python", "script"), ("set_study_zone", "Le Lavandou")]
    assert journal[0].startswith("projet vide")


# ── Clients ────────────────────────────────────────────────────────────────

def _lignes_fixture(nom: str) -> bytes:
    return (FIXTURES / nom).read_bytes()


def test_client_agent_envoie_le_formulaire_et_lit_le_flux():
    ouvreur = Ouvreur([_lignes_fixture("s1_reussi.sse")])
    agent = ClientAgent("https://hub.example.fr/agent/", jeton="qgis_abc", ouvrir=ouvreur)
    journal: list[dict] = []
    tour = agent.envoyer("Charge les bâtis sur Aix-en-Provence", "sess-1", journal=journal)
    req = ouvreur.requetes[0]
    assert req.full_url == "https://hub.example.fr/agent/chat"
    assert urllib.parse.parse_qs(req.data.decode()) == {
        "message": ["Charge les bâtis sur Aix-en-Provence"], "session_id": ["sess-1"]}
    assert req.get_header("Authorization") == "Bearer qgis_abc"
    assert req.get_header("Cookie") == "hub_api_key=qgis_abc"
    assert tour.outils() == ["set_study_zone", "smart_load"] and tour.termine
    assert journal[-1] == {"done": True}


def test_client_agent_direct_avec_utilisateur_delegue():
    ouvreur = Ouvreur([b'data: {"done": true}\n\n'])
    ClientAgent("http://agent:8888", jeton="k", utilisateur="nic01asfr", ouvrir=ouvreur).envoyer("x", "s")
    assert ouvreur.requetes[0].get_header("X-hub-proxy-user") == "nic01asfr"


def test_client_agent_coupe_au_dela_de_la_duree_et_demande_l_arret():
    temps = iter([0.0] + [1000.0] * 50)
    ouvreur = Ouvreur([_lignes_fixture("s1_reussi.sse"), b"{}"])
    agent = ClientAgent("http://agent", ouvrir=ouvreur, horloge=lambda: next(temps))
    tour = agent.envoyer("m", "sess/2", duree_max_s=10)
    assert tour.coupe_par_le_banc
    assert ouvreur.requetes[1].full_url == "http://agent/chat/sess%2F2/stop"


def test_client_agent_connexion_impossible():
    def ouvrir(req, timeout=None):
        raise OSError("connexion refusee")

    with pytest.raises(ErreurHttp, match="connexion refusee"):
        ClientAgent("http://agent", ouvrir=ouvrir).envoyer("m", "s")


def test_client_hub_point_de_retour_et_restauration():
    ouvreur = Ouvreur([b'{"ok": true, "study_id": "s-bac"}', b'{"ok": true}'])
    hub = ClientHub("https://hub.example.fr", "qgis_abc", ouvrir=ouvreur)
    hub.point_de_retour("banc-1", "ck-1")
    hub.restaurer("banc-1", "ck-1", "s-bac")
    assert [r.full_url for r in ouvreur.requetes] == [
        "https://hub.example.fr/sessions/banc-1/checkpoint",
        "https://hub.example.fr/sessions/banc-1/restore-checkpoint"]
    assert json.loads(ouvreur.requetes[1].data) == {"checkpoint_id": "ck-1", "study_id": "s-bac"}


def test_client_hub_point_de_retour_refuse():
    hub = ClientHub("https://hub", "k", ouvrir=Ouvreur([b'{"ok": false, "reason": "no_active_study"}']))
    with pytest.raises(ErreurHttp, match="no_active_study"):
        hub.point_de_retour("b", "c")
