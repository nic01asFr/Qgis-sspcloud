"""Ligne de couche de la L2 (défaut D3 du 2026-09-26).

Sans l'id de couche dans la L2, le modèle appelait get_project_info avant
chaque clip_to_study_zone (qui exige `layer_id`) : un appel LLM de plus par
découpage, constaté en production. On vérifie ici le format de la ligne et
qu'un projet courant de 15 couches tient dans le budget de la liste.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent import context_budget as cb  # noqa: E402
from agent.memory import _ligne_couche_l2  # noqa: E402

_ID = "Routes_Aix_eba26bdb_ab84_5cac_8a69_f2f22f16389b"


def _ly(**k) -> dict:
    base = {"id": _ID, "name": "Routes Aix", "geometry_type": "LineString",
            "feature_count": 12876, "crs": "EPSG:2154", "origine": "fichier",
            "fichier_present": True}
    base.update(k)
    return base


def test_la_ligne_porte_l_id_exact() -> None:
    ligne = _ligne_couche_l2(_ly(), "EPSG:2154", "Aix-en-Provence")
    assert ligne == ("  • Routes Aix (LineString, 12876 entités, fichier) "
                     f"id={_ID}")


def test_le_crs_n_apparait_que_s_il_differe_du_projet() -> None:
    assert "EPSG:4326" in _ligne_couche_l2(_ly(crs="EPSG:4326"), "EPSG:2154", None)
    assert "EPSG:2154" not in _ligne_couche_l2(_ly(), "EPSG:2154", None)


def test_nature_de_la_couche() -> None:
    assert "en mémoire, perdue au redémarrage" in _ligne_couche_l2(
        _ly(origine="memoire"), "EPSG:2154", None)
    assert "service distant" in _ligne_couche_l2(
        _ly(origine="service distant"), "EPSG:2154", None)
    assert "fichier introuvable" in _ligne_couche_l2(
        _ly(fichier_present=False), "EPSG:2154", None)
    # Pont plus ancien, sans champ origine : on n'invente rien.
    sans = _ly()
    del sans["origine"]
    assert _ligne_couche_l2(sans, "EPSG:2154", None) == (
        f"  • Routes Aix (LineString, 12876 entités) id={_ID}")


def test_decoupage_reconnu_par_le_nom_de_sortie_du_pont() -> None:
    sortie = _ly(name="routes_aix_aix_en_provence")
    assert "découpée à Aix-en-Provence" in _ligne_couche_l2(
        sortie, "EPSG:2154", "Aix-en-Provence")


def test_une_couche_brute_nommee_d_apres_la_zone_n_est_pas_decoupee() -> None:
    # smart_load nomme « Bâti BDTOPO - Aix-en-Provence » : finit par la zone,
    # mais c'est l'emprise rectangulaire, pas le contour communal.
    brute = _ly(name="Bâti BDTOPO - Aix-en-Provence")
    assert "découpée" not in _ligne_couche_l2(brute, "EPSG:2154", "Aix-en-Provence")
    # En mémoire : pas une sortie de clip_to_study_zone (qui écrit un fichier).
    mem = _ly(name="routes_aix_aix_en_provence", origine="memoire")
    assert "découpée" not in _ligne_couche_l2(mem, "EPSG:2154", "Aix-en-Provence")


def test_quinze_couches_courantes_tiennent_dans_le_budget_de_liste() -> None:
    noms = [f"Bâti BDTOPO - Commune {i}" for i in range(cb.PLAFOND_NB_COUCHES_L2)]
    cout = sum(
        cb.estimer_tokens(_ligne_couche_l2(
            _ly(name=n, id=f"{n.replace(' ', '_')}_7e7ad858_e700_5ab4_9dec_2cb2a9784e5f"),
            "EPSG:2154", None) + "\n")
        for n in noms
    )
    assert cout <= cb.PLAFOND_COUCHES_L2
