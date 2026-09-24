"""Garde-fou bac a sable : le banc refuse toute etude qui n'en est pas un.

Les doublures ci-dessous enregistrent chaque appel : on verifie qu'en cas de
refus, RIEN n'a ete envoye a l'agent ni au pont.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from evals import run
from evals.garde_fou import (
    RefusGardeFou, est_bac_a_sable, normaliser_nom, verifier_active, verifier_etude,
)
from evals.modele import Tour

BAC = {"id": "s-bac", "name": "bac-a-sable banc", "status": "active", "origin": "test"}
UTILISATEUR = {"id": "s-user", "name": "Saint-Martin — potentiel éolien", "status": "active", "origin": "user"}


@pytest.mark.parametrize("nom", ["bac-a-sable", "Bac à sable - banc", "BAC_A_SABLE nightly", "bac a sable"])
def test_noms_de_bac_a_sable_acceptes(nom):
    assert est_bac_a_sable(nom)


@pytest.mark.parametrize("nom", ["", "Aix bâti", "mon bac-a-sable", "bac-sable", "sable", "étude bac-a-sable"])
def test_noms_refuses(nom):
    assert not est_bac_a_sable(nom)


def test_normaliser_nom():
    assert normaliser_nom("  Bac à Sable_Banc ") == "bac-a-sable-banc"


def test_study_obligatoire():
    with pytest.raises(RefusGardeFou, match="--study"):
        verifier_etude("", BAC)


def test_etude_introuvable():
    with pytest.raises(RefusGardeFou, match="introuvable"):
        verifier_etude("s-bac", None)


def test_etude_utilisateur_refusee():
    with pytest.raises(RefusGardeFou, match="bac-a-sable"):
        verifier_etude("s-user", UTILISATEUR)


def test_etude_archivee_refusee():
    with pytest.raises(RefusGardeFou, match="archived"):
        verifier_etude("s-bac", {**BAC, "status": "archived"})


def test_identifiant_different_refuse():
    with pytest.raises(RefusGardeFou):
        verifier_etude("s-bac", {**BAC, "id": "autre"})


def test_origine_user_acceptee_avec_avertissement():
    assert verifier_etude("s-bac", BAC) == ""
    assert "origin=test" in verifier_etude("s-bac", {**BAC, "origin": "user"})


def test_etude_active_differente_refusee():
    with pytest.raises(RefusGardeFou, match="active"):
        verifier_active("s-bac", UTILISATEUR)
    with pytest.raises(RefusGardeFou):
        verifier_active("s-bac", None)
    verifier_active("s-bac", BAC)


# ── Doublures ──────────────────────────────────────────────────────────────

class HubFactice:
    def __init__(self, etudes: dict, active: str | None):
        self.etudes = etudes
        self.active = active
        self.appels: list[tuple] = []

    def etude(self, sid):
        self.appels.append(("etude", sid))
        return self.etudes.get(sid)

    def etude_active(self):
        self.appels.append(("etude_active",))
        return self.etudes.get(self.active) if self.active else None

    def activer(self, sid):
        self.appels.append(("activer", sid))
        self.active = sid

    def point_de_retour(self, session, ckpt):
        self.appels.append(("point_de_retour", ckpt))
        return {"ok": True}

    def restaurer(self, session, ckpt, sid):
        self.appels.append(("restaurer", ckpt, sid))


class AgentFactice:
    def __init__(self):
        self.messages: list[str] = []

    def envoyer(self, message, session_id, duree_max_s=900, profil="", journal=None):
        self.messages.append(message)
        if journal is not None:
            journal.append({"done": True})
        return Tour(message=message, reponse="Bonjour, je peux t'aider à cartographier ton territoire.",
                    iterations=1, termine=True)

    def etiqueter(self, *a):
        pass


class PontFactice:
    def __init__(self):
        self.commandes: list[tuple] = []

    def commande(self, action, params, timeout=120):
        self.commandes.append((action, params))
        if action == "execute_python" and "result[\"etat\"]" in params.get("code", ""):
            return {"success": True, "result": {"etat": {"zone": None, "couches": [], "inspections": {}}}}
        return {"success": True, "result": {}}


def _main(argv, hub, agent, pont, tmp_path: Path) -> int:
    return run.main(
        [*argv, "--scenarios", str(Path(run.__file__).parent / "scenarios"), "--filtre", "^C1-",
         "--agent-url", "https://hub.example.fr/agent", "--hub-url", "https://hub.example.fr",
         "--bridge", "http://localhost:8080", "--jeton", "qgis_test", "--sortie", str(tmp_path)],
        fabrique_hub=lambda *a: hub, fabrique_agent=lambda *a: agent, fabrique_pont=lambda args: pont,
    )


def test_main_refuse_sans_study(tmp_path):
    hub, agent, pont = HubFactice({"s-bac": BAC}, "s-bac"), AgentFactice(), PontFactice()
    assert _main([], hub, agent, pont, tmp_path) == run.CODE_REFUS
    assert hub.appels == [] and agent.messages == [] and pont.commandes == []


def test_main_refuse_une_etude_utilisateur(tmp_path):
    hub = HubFactice({"s-user": UTILISATEUR}, "s-user")
    agent, pont = AgentFactice(), PontFactice()
    assert _main(["--study", "s-user"], hub, agent, pont, tmp_path) == run.CODE_REFUS
    assert agent.messages == [] and pont.commandes == []
    assert not any(a[0] in ("point_de_retour", "restaurer", "activer") for a in hub.appels)


def test_main_refuse_si_l_etude_active_est_une_autre(tmp_path):
    hub = HubFactice({"s-bac": BAC, "s-user": UTILISATEUR}, "s-user")
    agent, pont = AgentFactice(), PontFactice()
    assert _main(["--study", "s-bac"], hub, agent, pont, tmp_path) == run.CODE_REFUS
    assert agent.messages == [] and pont.commandes == []


def test_main_active_le_bac_a_sable_sur_demande_et_joue(tmp_path):
    hub = HubFactice({"s-bac": BAC, "s-user": UTILISATEUR}, "s-user")
    agent, pont = AgentFactice(), PontFactice()
    code = _main(["--study", "s-bac", "--activer-etude", "--repeat", "2"], hub, agent, pont, tmp_path)
    assert code == 0
    assert ("activer", "s-bac") in hub.appels
    assert agent.messages == ["Bonjour !", "Bonjour !"]
    restaurations = [a for a in hub.appels if a[0] == "restaurer"]
    assert len(restaurations) == 3  # avant chaque repetition, puis a la fin
    assert (tmp_path / "rapport.json").exists() and (tmp_path / "rapport.md").exists()
    assert (tmp_path / "brut" / "C1-salutation" / "r1-t1.jsonl").exists()


def test_bascule_d_etude_en_cours_de_banc_arrete_tout(tmp_path):
    hub = HubFactice({"s-bac": BAC, "s-user": UTILISATEUR}, "s-bac")
    agent, pont = AgentFactice(), PontFactice()

    class AgentQuiBascule(AgentFactice):
        def envoyer(self, *a, **k):
            hub.active = "s-user"  # l'utilisateur change d'etude pendant le banc
            return super().envoyer(*a, **k)

    agent = AgentQuiBascule()
    code = _main(["--study", "s-bac", "--repeat", "3"], hub, agent, pont, tmp_path)
    assert code == run.CODE_REFUS
    assert agent.messages == ["Bonjour !"]
    # Aucune restauration apres la bascule : on ne touche pas l'etude utilisateur.
    dernier_actif = max(i for i, a in enumerate(hub.appels) if a[0] == "etude_active")
    assert not any(a[0] == "restaurer" for a in hub.appels[dernier_actif:])


def test_dry_run_sans_reseau(tmp_path, capsys):
    assert run.main(["--dry-run"]) == 0
    assert "scenario(s) valides" in capsys.readouterr().out
