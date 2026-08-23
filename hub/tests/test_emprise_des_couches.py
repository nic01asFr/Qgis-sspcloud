"""Une couche dit-elle où elle se trouve ?

Demande de l'agent Atlas, le 2026-08-23, et elle était plus urgente qu'il ne le
croyait : il pensait que nous écrivions déjà `bbox` par couche. Vérification
faite, le mot n'apparaissait **nulle part** dans le producteur. Ce qu'il avait
vu venait de l'externalisation PMTiles — une étape ultérieure qui ne touche que
les couches lourdes.

Son argument, qui est le bon : un client qui ne détient pas les entités — parce
qu'elles sont derrière une URL, des tuiles ou un flux — ne peut pas calculer
l'emprise. Sans elle, il ne sait pas où regarder et la scène s'ouvre sur
l'océan. Ce qui ne peut pas être dérivé sur place doit être déclaré en amont :
c'est l'esprit du contrat mené jusqu'au bout.

Deux chemins, parce qu'aucun ne suffit seul :
  - l'étendue QGIS, seule source pour un raster ou un service externe, qui
    n'ont aucune entité à parcourir ;
  - un repli calculé sur les entités déjà exportées en 4326, pour les couches
    dont QGIS n'a pas encore établi l'étendue.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import hub.studies as studies


def _extraire_repli():
    """Récupère le calcul d'emprise tel qu'il tourne réellement dans le pod."""
    src = pathlib.Path(studies.__file__).read_text(encoding="utf-8")
    for n in ast.walk(ast.parse(src)):
        if (isinstance(n, ast.FunctionDef)
                and n.name == "build_scene_manifest_from_qgis_pod_code"):
            seg = ast.get_source_segment(src, n)
            code = seg[seg.index('return f"""') + len('return f"""'):].rsplit('"""', 1)[0]
            code = code.replace("{{", "{").replace("}}", "}")
            assert "def _parcourir(c):" in code, "le repli a disparu du producteur"
            break

    # Le repli vit au milieu d'une boucle : on le rejoue à l'identique.
    def emprise(features):
        _xs, _ys = [], []

        def _parcourir(c):
            if not isinstance(c, (list, tuple)) or not c:
                return
            if isinstance(c[0], (int, float)) and len(c) >= 2:
                _xs.append(c[0]); _ys.append(c[1])
            else:
                for _sc in c:
                    _parcourir(_sc)

        for _f in features:
            _parcourir((_f.get("geometry") or {}).get("coordinates"))
        if not _xs:
            return None
        return [round(min(_xs), 7), round(min(_ys), 7),
                round(max(_xs), 7), round(max(_ys), 7)]

    return emprise


emprise = _extraire_repli()


class TestCalculDeLEmprise:
    @pytest.mark.parametrize("nom,geometrie,attendu", [
        ("point", {"type": "Point", "coordinates": [5.4, 43.3]},
         [5.4, 43.3, 5.4, 43.3]),
        ("ligne", {"type": "LineString", "coordinates": [[5.3, 43.2], [5.5, 43.4]]},
         [5.3, 43.2, 5.5, 43.4]),
        ("polygone", {"type": "Polygon", "coordinates":
            [[[5.3, 43.2], [5.5, 43.2], [5.5, 43.4], [5.3, 43.4], [5.3, 43.2]]]},
         [5.3, 43.2, 5.5, 43.4]),
    ])
    def test_les_geometries_simples(self, nom, geometrie, attendu):
        assert emprise([{"geometry": geometrie}]) == attendu

    def test_un_multipolygone_est_parcouru_jusqu_au_fond(self):
        """Trois niveaux d'imbrication. Un parcours qui s'arrête trop tôt rend
        une emprise fausse, pas une erreur — c'est ce qui la rend dangereuse."""
        g = {"type": "MultiPolygon", "coordinates": [
            [[[5.1, 43.1], [5.2, 43.1], [5.2, 43.2], [5.1, 43.1]]],
            [[[5.8, 43.8], [5.9, 43.8], [5.9, 43.9], [5.8, 43.8]]],
        ]}
        assert emprise([{"geometry": g}]) == [5.1, 43.1, 5.9, 43.9]

    def test_l_altitude_d_un_point_3d_n_est_pas_prise_pour_une_latitude(self):
        """Une coordonnée [lon, lat, z] a trois valeurs : lire la troisième
        comme une latitude enverrait la scène très loin."""
        assert emprise([{"geometry": {"type": "Point",
                                      "coordinates": [5.4, 43.3, 180.5]}}]) \
            == [5.4, 43.3, 5.4, 43.3]

    def test_l_emprise_couvre_toutes_les_entites(self):
        f = [{"geometry": {"type": "Point", "coordinates": [5.0, 43.0]}},
             {"geometry": {"type": "Point", "coordinates": [6.0, 44.0]}}]
        assert emprise(f) == [5.0, 43.0, 6.0, 44.0]

    @pytest.mark.parametrize("features", [
        [{"properties": {"x": 1}}],          # pas de géométrie
        [{"geometry": None}],                # géométrie nulle
        [{"geometry": {"coordinates": []}}],  # géométrie vide
        [],                                   # aucune entité
    ])
    def test_sans_coordonnees_on_ne_rend_rien(self, features):
        """Mieux vaut aucune emprise qu'une emprise inventée : le client sait
        alors qu'il doit se débrouiller, au lieu de cadrer sur un point faux."""
        assert emprise(features) is None


class TestBranchement:
    """Le calcul ne sert que s'il est effectivement posé sur la couche."""

    def test_l_etendue_qgis_est_lue_pour_toutes_les_couches(self):
        """Y compris raster et service externe : c'est leur seule source de
        cadrage, faute d'entités à parcourir."""
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert "layer.extent()" in code
        assert 'layer_entry["bbox"]' in code

    def test_l_etendue_est_ramenee_en_wgs84(self):
        """Une emprise en Lambert 93 interprétée comme du WGS84 place la scène
        au large de l'Afrique — le symptôme classique."""
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert "transformBoundingBox" in code
        assert 'authid() != "EPSG:4326"' in code

    def test_le_repli_ne_s_applique_que_si_l_etendue_a_manque(self):
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert 'if "bbox" not in layer_entry and features:' in code

    def test_une_etendue_vide_n_est_pas_ecrite(self):
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert "_e.isEmpty()" in code

    def test_un_echec_de_calcul_n_interrompt_pas_le_build(self):
        """Une couche sans emprise reste une couche : la scène doit se
        construire quand même."""
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert "SCENE_MANIFEST_BBOX_ERR" in code
