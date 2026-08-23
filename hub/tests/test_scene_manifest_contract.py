"""Le Scene Manifest que nous produisons est-il lisible par les autres ?

Contexte (2026-08-23). Nos scenes n'etaient ouvrables par aucun consommateur
externe : le contrat publie exige `version` a la racine, nous ecrivions
`manifest_version`. Une seule absence, mais eliminatoire -- et invisible, parce
que rien ne validait. Ces tests rendent la regression bruyante.

Le schema de reference est embarque dans le paquet (hub/schemas/), copie de
https://nic01asfr.github.io/Widgets-Grist/schemas/scene-manifest-0.2.2.schema.json
Copie et non telechargee : il sert aussi dans le chemin de production, ou l'on
ne depend pas du reseau. A resynchroniser quand le contrat publie bouge.

Ce qui n'est PAS teste ici : qu'Atlas sache charger les couches. Il ne le sait
pas -- il ne resout que des tables Grist, pas des fichiers GeoJSON. Valider le
schema est necessaire, pas suffisant. Voir docs/interop-atlas-scene-manifest.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import hub.studies as studies

from hub import contracts

_SCHEMA = contracts.schema_scene()


def _scene_exemple() -> dict:
    """Une scene dans la forme exacte que le pod ecrit sur le PVC."""
    return {
        "version": "0.2.2",
        "manifest_version": "V0.2",
        "manifest_id": "56f50530-ce80-4a82-a8d4-29cecee34b80",
        "title": "Scene Manifest",
        "source": {
            "project_qgs": "/data/studies/abc/projects/def/project.qgz",
            "study_id": "abc", "project_id": "def",
        },
        "layers": [{
            "id": "batiments__bd_topo_",
            "name": "Bâtiments (BD TOPO)",
            "order": 0,
            "geometry_type": "polygon",
            "visible": True,
            "style": {
                "qml_source": None,
                "declarative": {"kind": "single", "color": "#1d70b8", "opacity": 1.0},
            },
            "geojson_path": "/data/studies/abc/projects/def/scene_layers/b.geojson",
            "n_features": 14270,
            "geojson_size_bytes": 21196832,
            "crs": "EPSG:4326",
        }],
    }


def test_le_code_produit_annonce_la_version_du_contrat():
    """Sans `version`, le contrat publie rejette la scene -- pour cela seul."""
    code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
    assert '"version": "0.2.2"' in code


def test_chaque_couche_porte_son_ordre_d_empilement():
    """L'ordre etait implicite dans la position en liste. Un consommateur qui
    trie les couches ne pouvait pas le retrouver."""
    code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
    assert '"order": i' in code


def test_le_code_produit_reste_du_python_valide():
    """Il est assemble par f-string : une indentation fausse ne se voit qu'ici."""
    code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
    compile(code, "<pod>", "exec")


def test_la_scene_satisfait_les_exigences_du_contrat_publie():
    """Verification sans dependance : les champs que le schema declare requis."""
    scene = _scene_exemple()
    manquants = [c for c in _SCHEMA.get("required", []) if c not in scene]
    assert manquants == [], f"champs requis absents : {manquants}"

    exiges_couche = _SCHEMA["definitions"]["layer"].get("required", [])
    for i, couche in enumerate(scene["layers"]):
        absents = [c for c in exiges_couche if c not in couche]
        assert absents == [], f"couche {i} : champs requis absents {absents}"


def test_la_scene_valide_contre_le_schema_complet():
    """Validation integrale, si jsonschema est disponible dans l'environnement."""
    jsonschema = pytest.importorskip("jsonschema")
    erreurs = sorted(
        jsonschema.Draft7Validator(_SCHEMA).iter_errors(_scene_exemple()),
        key=lambda e: list(e.path),
    )
    assert not erreurs, "\n".join(
        f"{'/'.join(map(str, e.path)) or '(racine)'} : {e.message}" for e in erreurs
    )


def test_une_scene_sans_version_est_bien_refusee():
    """Le garde-fou lui-meme doit mordre, sinon il ne prouve rien."""
    jsonschema = pytest.importorskip("jsonschema")
    scene = _scene_exemple()
    del scene["version"]
    erreurs = list(jsonschema.Draft7Validator(_SCHEMA).iter_errors(scene))
    assert erreurs, "le schema accepte une scene sans version : garde-fou inutile"
