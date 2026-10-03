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


def _source_agent():
    return open(qa.__file__, encoding="utf-8").read()


def test_la_consigne_en_fait_le_reflexe_sans_analyse_d_office():
    regle = _source_agent().split("2bis. ")[1].split("Les deux autres cas")[0]
    assert "UN SEUL appel `charger_sur_commune(id, commune)`" in regle
    assert "la commune SEULE" in regle
    assert "sans les lancer" in regle


def test_la_consigne_n_enseigne_plus_le_chemin_en_deux_temps():
    """Essai du 2026-10-03 : un passage sur trois, smart_load puis
    clip_to_study_zone. La regle 2bis disait les deux chemins a la suite, la
    regle 3 proposait native:clip sur un contour pris a geo.api.gouv.fr."""
    source = _source_agent()
    regle = source.split("2bis. ")[1].split("Si le catalogue ne contient PAS")[0]
    assert "Les deux autres cas, et eux seuls" in regle
    assert "une couche DÉJÀ chargée" in regle
    assert "exige d'abord `clip_to_study_zone" not in regle
    bbox = source.split("## 3. BBOX d'extraction")[1].split("\n# ")[0]
    assert "native:clip" not in bbox and "geo.api.gouv.fr" not in bbox
    assert "charger_sur_commune" in bbox


def test_le_garde_fou_de_zone_lit_la_commune():
    assert qa.QGISAgent._ZONE_SENSITIVE_TOOLS["charger_sur_commune"] == ["commune"]


def test_add_from_catalog_n_est_plus_montre_au_modele():
    assert "add_from_catalog" not in po.SOCLE
    assert "add_from_catalog" in po.OUTILS_MASQUES
