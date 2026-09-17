"""« Une mise a jour est-elle disponible ? » doit avoir une reponse.

Savoir ce qui s'execute ne suffit pas : il faut aussi ce que le registre
publie sous le tag demande. Verifie le 2026-09-17 sur les trois briques de
production : l'`imageID` rapporte par le kubelet est exactement le
`Docker-Content-Digest` rendu par le registre, donc les deux se comparent
directement.

Ce socle est ce qui permettra a l'application de signaler une mise a jour et
a l'utilisateur de la declencher : les trois briques sont en
`imagePullPolicy: Always` sur un tag mobile, donc un redemarrage tire la
nouvelle image, et les donnees vivent sur le PVC.
"""
from __future__ import annotations

import pytest

from hub import version as v


@pytest.fixture(autouse=True)
def _sans_cache(monkeypatch):
    monkeypatch.setattr(v, "_cache", {"t": 0.0, "data": None})
    monkeypatch.setattr(v, "_cache_publie", {})


def _pods(**briques) -> dict:
    return {
        nom: {"digest": digest,
              "reference": f"ghcr.io/proprio/qgis-{nom}:latest"}
        for nom, digest in briques.items()
    }


# ── Lire une reference d'image ───────────────────────────────────────────


def test_le_depot_et_le_tag_sont_extraits():
    assert v._depot_et_tag("ghcr.io/proprio/qgis-hub:latest") == (
        "proprio/qgis-hub", "latest")


def test_un_tag_absent_vaut_latest():
    assert v._depot_et_tag("ghcr.io/proprio/qgis-hub") == (
        "proprio/qgis-hub", "latest")


def test_une_reference_par_empreinte_garde_son_depot():
    assert v._depot_et_tag("ghcr.io/proprio/qgis-hub@sha256:abc") == (
        "proprio/qgis-hub", "latest")


def test_un_autre_registre_n_est_pas_devine():
    """On ne sait interroger que ghcr.io : deviner serait pire que se taire."""
    assert v._depot_et_tag("docker.io/bibliotheque/image:1") is None
    assert v._depot_et_tag("") is None


# ── L'ecart entre ce qui tourne et ce qui est publie ─────────────────────


def test_une_brique_a_jour_est_dite_a_jour(monkeypatch):
    monkeypatch.setattr(v, "_digest_publie", lambda d, t: "sha256:pareil")
    briques = v._etat_des_briques(_pods(hub="sha256:pareil"))
    assert briques["hub"]["a_jour"] is True


def test_une_brique_en_retard_est_signalee(monkeypatch):
    monkeypatch.setattr(v, "_digest_publie", lambda d, t: "sha256:neuf")
    briques = v._etat_des_briques(_pods(hub="sha256:ancien"))
    assert briques["hub"]["a_jour"] is False
    assert briques["hub"]["publie"] == "sha256:neuf"


def test_l_application_recoit_la_liste_des_briques_a_mettre_a_jour(monkeypatch):
    monkeypatch.setattr(v, "_namespace", lambda: "user-essai")
    monkeypatch.setattr(v, "_empreintes_en_cours", lambda ns: _pods(
        hub="sha256:ancien", agent="sha256:pareil", workspace="sha256:pareil"))
    monkeypatch.setattr(v, "_digest_publie",
                        lambda d, t: "sha256:ancien" if "hub" in d else "sha256:pareil")

    etat = v.etat()

    assert etat["mise_a_jour_disponible"] == []
    monkeypatch.setattr(v, "_cache", {"t": 0.0, "data": None})
    monkeypatch.setattr(v, "_digest_publie", lambda d, t: "sha256:pareil")
    assert v.etat()["mise_a_jour_disponible"] == ["hub"]


# ── Un inconnu n'est pas un « a jour » ───────────────────────────────────


def test_une_brique_en_veille_ne_repond_pas_a_la_question(monkeypatch):
    monkeypatch.setattr(v, "_digest_publie", lambda d, t: "sha256:neuf")
    briques = v._etat_des_briques(
        {"workspace": {"digest": None, "reference": "ghcr.io/o/i:latest"}})
    assert briques["workspace"]["a_jour"] is None
    assert "veille" in briques["workspace"]["note"]


def test_un_registre_injoignable_ne_fait_pas_conclure(monkeypatch):
    monkeypatch.setattr(v, "_digest_publie", lambda d, t: None)
    briques = v._etat_des_briques(_pods(hub="sha256:quelque_chose"))
    assert briques["hub"]["a_jour"] is None
    assert "registre" in briques["hub"]["note"]


def test_un_registre_injoignable_ne_fait_pas_echouer(monkeypatch):
    """Le releve doit rendre ce qu'il sait, meme sans reponse du registre."""
    def _explose(url, timeout=None):
        raise OSError("reseau coupe")

    monkeypatch.setattr(v.urllib.request, "urlopen", _explose)
    assert v._digest_publie("proprio/image", "latest") is None


def test_l_empreinte_publiee_est_gardee_en_cache(monkeypatch):
    appels = []

    def _une_fois(url, timeout=None):
        appels.append(url)
        raise OSError("peu importe, on compte les appels")

    monkeypatch.setattr(v.urllib.request, "urlopen", _une_fois)
    v._digest_publie("proprio/image", "latest")
    v._digest_publie("proprio/image", "latest")
    assert len(appels) == 1, "le registre ne doit pas etre interroge en boucle"
