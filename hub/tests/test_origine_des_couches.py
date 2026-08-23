"""L'origine d'une couche survit-elle au passage dans le manifest ?

Jusqu'au 2026-08-23, non. Une couche WMS n'avait aucune origine : le manifest
declarait « une couche raster nommee X, en bleu » sans dire ou elle est. Une
couche WFS devenait une copie GeoJSON figee, le lien avec le flux perdu. Le
service compte pourtant 54 sources externes au catalogue (25 WFS, 8 WMS, 6 XYZ,
1 WMTS, 7 API), pre-chargees dans le profil QGIS.

La fonction testee vit dans le code PyQGIS genere pour le pod. On l'en extrait
plutot que de la reecrire ici : un test qui recopierait la logique ne
protegerait rien.

Elle porte aussi la CLASSE de la source, parce que producteur et consommateur
doivent en tirer des conclusions opposees :
  externe  le client lit directement, on ne copie rien
  atelier  le navigateur ne peut PAS y acceder -- la base est en ClusterIP --
           il faut materialiser avant publication
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import hub.studies as studies


def _extraire_origine():
    """Recupere `_origine` depuis la f-string du code genere pour le pod."""
    src = pathlib.Path(studies.__file__).read_text(encoding="utf-8")
    for n in ast.walk(ast.parse(src)):
        if (isinstance(n, ast.FunctionDef)
                and n.name == "build_scene_manifest_from_qgis_pod_code"):
            seg = ast.get_source_segment(src, n)
            code = seg[seg.index('return f"""') + len('return f"""'):].rsplit('"""', 1)[0]
            code = code.replace("{{", "{").replace("}}", "}")
            debut = code.index("    def _origine(couche):")
            fin = code.index("    # Defaults StyleDeclarative")
            bloc = "\n".join(
                l[4:] if l.startswith("    ") else l
                for l in code[debut:fin].splitlines()
            )
            espace: dict = {}
            exec(bloc, espace)  # noqa: S102 — c'est le code teste
            return espace["_origine"]
    raise AssertionError("le producteur de scene manifest a disparu")


origine = _extraire_origine()


class CoucheFactice:
    """Le minimum qu'une couche QGIS expose pour dire d'ou elle vient."""

    def __init__(self, fournisseur: str, datasource: str):
        self._f, self._d = fournisseur, datasource

    def providerType(self):
        return self._f

    def source(self):
        return self._d


class TestSourcesExternes:
    """Celles-la ne doivent jamais etre copiees : le client les lit lui-meme."""

    def test_wms_garde_son_flux(self):
        """Le cas reel du service : le MNT IGN."""
        o = origine(CoucheFactice("wms",
            "url=https://data.geopf.fr/wms-r&layers=ELEVATION.ELEVATIONGRIDCOVERAGE"
            ".HIGHRES&format=image/tiff&crs=EPSG:2154&styles="))
        assert o["type"] == "wms" and o["classe"] == "externe"
        assert o["url"] == "https://data.geopf.fr/wms-r"
        assert o["layers"] == "ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES"
        assert o["crs"] == "EPSG:2154"

    def test_les_tuiles_xyz_ne_sont_pas_confondues_avec_du_wms(self):
        """QGIS range les XYZ sous le fournisseur wms ; seul `type=xyz` les
        distingue. Sans cette lecture, une couche OSM serait annoncee comme un
        service WMS et aucun client ne saurait l'afficher."""
        o = origine(CoucheFactice("wms",
            "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19"))
        assert o["type"] == "xyz"
        assert "{z}/{x}/{y}" in o["url"]

    def test_wmts_est_distingue_du_wms(self):
        o = origine(CoucheFactice("wms",
            "url=https://data.geopf.fr/wmts&layers=ORTHO&tileMatrixSet=PM"))
        assert o["type"] == "wmts" and o["tileMatrixSet"] == "PM"

    def test_wfs_garde_son_typename(self):
        o = origine(CoucheFactice("WFS",
            "url=https://data.geopf.fr/wfs&typename=BDTOPO_V3:batiment&srsname=EPSG:2154"))
        assert o["type"] == "wfs" and o["classe"] == "externe"
        assert o["typename"] == "BDTOPO_V3:batiment"

    @pytest.mark.parametrize("fournisseur,ds", [
        ("wms", "url=https://x/wms&layers=a"),
        ("WFS", "url=https://x/wfs&typename=a"),
        ("wms", "type=xyz&url=https://x/{z}/{x}/{y}.png"),
    ])
    def test_toutes_sont_de_classe_externe(self, fournisseur, ds):
        assert origine(CoucheFactice(fournisseur, ds))["classe"] == "externe"


class TestSourcesDAtelier:
    """Celles-la ne traversent pas : un navigateur ne peut pas les atteindre."""

    def test_une_base_est_marquee_atelier(self):
        o = origine(CoucheFactice("postgres",
            "dbname='cerema' host=postgres-cerema user='admin' table=\"public\".\"b\""))
        assert o["type"] == "base" and o["classe"] == "atelier"

    def test_aucun_identifiant_n_est_jamais_recopie(self):
        """Un manifest est fait pour circuler. Une datasource PostGIS porte
        l'hote, l'utilisateur et le mot de passe : rien de tout cela ne doit
        s'y retrouver, meme par inadvertance."""
        o = origine(CoucheFactice("postgres",
            "dbname='cerema' host=postgres-cerema.interne port=5432 "
            "user='admin' password='tres-secret-42' sslmode=require "
            "table=\"public\".\"batiments\" (geom)"))
        rendu = repr(o)
        for interdit in ("tres-secret-42", "password", "host", "user",
                         "postgres-cerema.interne", "5432", "sslmode"):
            assert interdit not in rendu, f"« {interdit} » a fuite dans le manifest"

    @pytest.mark.parametrize("fournisseur", ["ogr", "gdal", "delimitedtext"])
    def test_un_fichier_local_est_d_atelier(self, fournisseur):
        o = origine(CoucheFactice(fournisseur, "/data/studies/abc/data/x.gpkg"))
        assert o["classe"] == "atelier"

    def test_un_fichier_local_ne_divulgue_pas_son_chemin(self):
        """Le chemin PVC n'apprend rien a un client et decrit notre
        arborescence interne."""
        o = origine(CoucheFactice("ogr", "/data/studies/abc/data/secret.gpkg"))
        assert "secret" not in repr(o) and "/data/" not in repr(o)

    def test_une_couche_temporaire_est_d_atelier(self):
        assert origine(CoucheFactice("memory", "Point?crs=EPSG:4326"))["classe"] == "atelier"


class TestRobustesse:
    def test_un_fournisseur_inconnu_ne_fait_pas_echouer_le_build(self):
        o = origine(CoucheFactice("un_truc_futur", "peu importe"))
        assert o["classe"] == "atelier", "dans le doute, on ne promet pas l'acces"

    def test_une_couche_muette_rend_None_au_lieu_de_lever(self):
        class Muette:
            def providerType(self): raise RuntimeError("couche invalide")
            def source(self): return ""
        assert origine(Muette()) is None

    def test_la_couche_porte_son_origine_dans_le_manifest(self):
        """Le branchement lui-meme : sans lui, la traduction ne servirait a rien."""
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert "_src = _origine(layer)" in code
        assert 'layer_entry["source"] = _src' in code
