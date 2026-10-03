"""« Charge le bati sur Aix » : un seul appel, le rendu attendu (2026-10-03).

Vecu sur nic01asfr : quatre messages et un redemarrage de QGIS (memoire
saturee) pour obtenir le bati decoupe a la commune, Aix seule et un
affichage propre. L'outil du workspace `charger_sur_commune` enchaine tout ;
l'agent doit le voir a chaque tour, le traiter comme long et mutant, et la
consigne doit en faire le reflexe, sans style ni analyse d'office.
"""
from __future__ import annotations

from agent import arriere_plan as ap
from agent import paquets_outils as po
from agent import qgis_agent as qa
from agent import texte_modele as tm


def test_l_outil_est_au_socle():
    assert "charger_sur_commune" in po.SOCLE


def test_l_outil_est_long_et_mutant():
    assert ap.est_long("charger_sur_commune")
    assert "charger_sur_commune" in qa._MUTATING_TOOLS


def test_l_outil_a_des_libelles_en_francais_courant():
    assert qa._libelle_outil("charger_sur_commune") == "Chargement et mise en forme sur la commune…"
    assert tm.LIBELLES_COURANTS["charger_sur_commune"] == "le chargement sur la commune"
    assert ap.libelle("charger_sur_commune", {}).startswith("Chargement sur la commune")


def test_la_consigne_en_fait_le_reflexe_sans_analyse_d_office():
    source = open(qa.__file__, encoding="utf-8").read()
    regle = source.split("2bis. ")[1].split("Pour charger des données")[0]
    assert "UN SEUL appel `charger_sur_commune(id, commune)`" in regle
    assert "la commune SEULE" in regle
    assert "sans les lancer" in regle
