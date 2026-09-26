"""Outils communs aux tests du banc. Aucun test ne touche le reseau."""
from __future__ import annotations

import pytest


@pytest.fixture
def etat_aix_reussi() -> dict:
    """Etat QGIS tel que la sonde le rendrait apres un S1/S2 reussi."""
    return {
        "zone": {"nom": "Aix-en-Provence", "bbox_4326": [5.2694, 43.4539, 5.5125, 43.6315]},
        "couches": [
            {"id": "c1", "nom": "commune_aix_en_provence", "valide": True, "crs": "EPSG:2154",
             "fournisseur": "ogr", "memoire": False, "source_type": "gpkg", "type": "vecteur",
             "nombre_entites": 1, "emprise_4326": [5.2694, 43.4539, 5.5125, 43.6315]},
            {"id": "c2", "nom": "bati_aix_en_provence", "valide": True, "crs": "EPSG:2154",
             "fournisseur": "ogr", "memoire": False, "source_type": "gpkg", "type": "vecteur",
             "nombre_entites": 68412, "emprise_4326": [5.2702, 43.4541, 5.5119, 43.6309]},
        ],
        "inspections": {
            "bati_aix_en_provence": {"lues": 68412, "tronque": False, "hors_bbox_zone": 0,
                                     "hors_contour": 0, "contour": "commune_aix_en_provence",
                                     "erreur": None},
        },
        "projet": {"fichier": "/data/studies/bac/projet.qgz", "modifie": True},
    }


@pytest.fixture
def etat_incident_s1() -> dict:
    """Etat mesure lors de l'incident S1 du 2026-09-24 (fiche par. 7 du plan)."""
    return {
        "zone": {"nom": "Aix-en-Provence", "bbox_4326": [5.2694, 43.4539, 5.5125, 43.6315]},
        "couches": [
            {"id": "c1", "nom": "batiments_bdtopo_aix_emprise", "valide": True, "crs": "EPSG:4326",
             "fournisseur": "WFS", "memoire": False, "source_type": "service", "type": "vecteur",
             "nombre_entites": 51450444, "emprise_4326": [-63.28, -21.77, 55.9, 51.97]},
            {"id": "c2", "nom": "bati_temp", "valide": False, "crs": "", "fournisseur": "memory",
             "memoire": True, "source_type": "memoire", "type": "vecteur", "nombre_entites": 0,
             "emprise_4326": None},
        ],
        "inspections": {},
        "projet": {"fichier": "", "modifie": True},
    }
