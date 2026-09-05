"""Notre visibilité ne parvenait à aucun consommateur.

Le producteur émettait `visible` à la racine de la couche. Ce champ **n'existe
pas** au contrat Scene Manifest 0.2.2, qui prévoit `visibility`. L'information
partait donc sous un nom que personne ne lit.

Conséquence mesurée sur Atlas, le runtime de référence : sans
`visibility.defaultVisible` explicite, `isBasemapLayer` masque toute couche de
plus de 2 500 entités. Une couche d'analyse — 14 270 objets sur le diagnostic
du parc bâti — s'ouvrait donc invisible. Le lecteur y voyait une carte vide,
c'est-à-dire un livrable raté, là où il n'y avait qu'un champ absent.

C'est le motif de la semaine sous une forme de plus : la donnée était là, la
déclaration était là, mais elle ne s'adressait à personne.

`visible` est conservé : nos surcharges de composant et deux filtres de rendu
le lisent (`main.py`, `actions/component_actions.py`). Le retirer casserait le
rendu interne pour réparer l'externe.
"""

from __future__ import annotations

import json
import re

import pytest

import hub.studies as studies


@pytest.fixture(scope="module")
def code_pod() -> str:
    return studies.build_scene_manifest_from_qgis_pod_code("abc123def456", "p1")


class TestCeQuiEstEmis:
    def test_le_champ_du_contrat_est_emis(self, code_pod):
        assert '"visibility"' in code_pod
        assert '"defaultVisible"' in code_pod

    def test_le_champ_interne_est_conserve(self, code_pod):
        """Deux filtres de rendu et les surcharges de composant le lisent.
        Le remplacer réparerait l'extérieur en cassant l'intérieur."""
        assert '"visible":' in code_pod

    def test_les_deux_disent_la_meme_chose(self, code_pod):
        """`visibility.defaultVisible` ne doit pas contredire `visible` : deux
        champs qui décrivent le même fait et divergent, c'est le défaut qu'on
        passe la semaine à corriger ailleurs."""
        i = code_pod.find('"visible":')
        bloc = code_pod[i:i + 700]
        assert bloc.count("layer.isVisible()") >= 2, (
            "les deux champs doivent dériver de la même source, pas de deux "
            "calculs indépendants"
        )

    def test_le_code_pod_reste_executable(self, code_pod):
        import ast
        ast.parse(code_pod)

    def test_les_accolades_sont_echappees(self, code_pod):
        """Le code est produit par une f-string : une accolade non doublée
        casserait la génération sans qu'un test de syntaxe le voie."""
        assert "{{" not in code_pod and "}}" not in code_pod


class TestConformiteAuContrat:
    def test_visibility_appartient_au_contrat_visible_non(self):
        """La raison d'être de ce correctif, vérifiée sur le schéma lui-même
        plutôt que supposée."""
        from hub import contracts
        schema = contracts.schema_scene()
        defs = schema.get("definitions") or schema.get("$defs") or {}
        props = defs.get("layer", {}).get("properties", {})
        assert "visibility" in props, "le contrat prévoit bien visibility"
        assert "visible" not in props, (
            "si `visible` entrait au contrat, ce correctif n'aurait plus lieu "
            "d'être — et ce test doit alors être revu, pas supprimé"
        )


class TestCeQuiNeNousConcernePas:
    def test_geometry_fields_n_est_pas_attendu_de_nous(self):
        """`source.geometry_fields` sert aux couches adossées à une table
        Grist, dont Atlas scanne les colonnes (`scanGeoTables`). Nos scènes
        publiées portent des sources `geojson_url` ou `pmtiles`, où la
        géométrie est intrinsèque au fichier.

        Ce test existe parce que j'avais listé ce champ comme un manque de
        notre producteur. Il n'en est pas un — et le noter évite qu'on
        l'implémente un jour par recopie de la liste."""
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "p")
        assert "geometry_fields" not in code
