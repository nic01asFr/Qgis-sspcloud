"""Le lecteur de couches accepte-t-il les quatre graphies rencontrees ?

Chaque cas correspond a un emetteur reel : notre hub, le contrat strict de
cerema-offre-de-service, la graphie des composants, celle d'Atlas. Aucune n'est
hypothetique -- toutes circulent dans des manifests deja ecrits.
"""

from __future__ import annotations

import pytest

from hub.scene_layers import chemin_fichier, origine_donnees, type_geometrie


class TestTypeGeometrie:
    @pytest.mark.parametrize("brut,attendu", [
        ("polygon", "polygon"),        # notre graphie
        ("Polygon", "polygon"),        # contrat strict, en litteral
        ("MultiPolygon", "polygon"),   # idem, geometrie multiple
        ("LineString", "line"),
        ("line", "line"),
        ("MultiPoint", "point"),
        ("raster", "raster"),
    ])
    def test_les_graphies_se_ramenent_a_la_forme_courte(self, brut, attendu):
        assert type_geometrie({"geometry_type": brut}) == attendu

    def test_le_champ_du_contrat_strict_est_lu_aussi(self):
        assert type_geometrie({"geomType": "Polygon"}) == "polygon"

    def test_notre_champ_prime_si_les_deux_sont_la(self):
        couche = {"geometry_type": "point", "geomType": "Polygon"}
        assert type_geometrie(couche) == "point"

    def test_sans_geometrie_on_rend_le_defaut_demande(self):
        assert type_geometrie({}, "polygon") == "polygon"
        assert type_geometrie({}) == "unknown"

    def test_une_geometrie_inconnue_reste_visible(self):
        """La remplacer par le defaut masquerait l'emetteur fautif."""
        assert type_geometrie({"geometry_type": "TIN"}) == "TIN"


class TestOrigineDonnees:
    def test_le_geojson_deja_present_prime_sur_tout(self):
        couche = {"geojson": {"type": "FeatureCollection", "features": []},
                  "geojson_path": "/data/x.geojson"}
        nature, valeur = origine_donnees(couche)
        assert nature == "inline" and valeur["type"] == "FeatureCollection"

    def test_un_geojson_vide_ne_compte_pas_comme_present(self):
        """Sinon on renverrait un inline vide au lieu d'aller lire le fichier."""
        couche = {"geojson": {}, "geojson_path": "/data/x.geojson"}
        assert origine_donnees(couche) == ("fichier", "/data/x.geojson")

    def test_notre_graphie_chemin_pvc(self):
        assert origine_donnees({"geojson_path": "/data/x.geojson"}) == (
            "fichier", "/data/x.geojson")

    def test_graphie_des_composants_source_type_path(self):
        couche = {"source": {"type": "geojson_path", "path": "/data/y.geojson"}}
        assert origine_donnees(couche) == ("fichier", "/data/y.geojson")

    def test_graphie_atlas_table_grist(self):
        assert origine_donnees({"source": {"table": "Batiments"}}) == (
            "table", "Batiments")

    def test_graphie_du_contrat_strict_data_url(self):
        assert origine_donnees({"data_url": "https://x/y.geojson"}) == (
            "url", "https://x/y.geojson")

    def test_source_en_simple_chaine_comme_l_autorise_le_schema(self):
        assert origine_donnees({"source": "https://x/y.geojson"})[0] == "url"
        assert origine_donnees({"source": "Batiments"}) == ("table", "Batiments")

    def test_une_couche_sans_origine_le_dit(self):
        assert origine_donnees({"id": "vide", "name": "Vide"}) is None

    def test_le_chemin_local_prime_sur_l_adresse_distante(self):
        couche = {"geojson_path": "/data/x.geojson", "data_url": "https://x/y"}
        assert origine_donnees(couche) == ("fichier", "/data/x.geojson")


class TestRegressionLignesRenduesEnAplat:
    """Les couches lineaires du hub etaient rendues comme des polygones.

    `geometry_to_layer_type` connaissait `linestring` et `multilinestring`,
    mais pas `line` -- qui est justement ce que notre producteur ecrit. Toute
    couche lineaire tombait donc dans le `fill` par defaut : routes, cours
    d'eau et reseaux dessines en aplat sur la carte.
    """

    @pytest.mark.parametrize("graphie", [
        "line", "LineString", "MultiLineString", "linestring", "multilinestring",
    ])
    def test_toutes_les_graphies_lineaires_donnent_un_trait(self, graphie):
        from hub.maplibre_style_mapper import geometry_to_layer_type
        assert geometry_to_layer_type(graphie) == ("line", "line-color")

    def test_ce_que_notre_producteur_ecrit_est_bien_couvert(self):
        """studies.py ecrit exactement ces trois valeurs."""
        from hub.maplibre_style_mapper import geometry_to_layer_type
        assert geometry_to_layer_type("point")[0] == "circle"
        assert geometry_to_layer_type("line")[0] == "line"
        assert geometry_to_layer_type("polygon")[0] == "fill"

    def test_une_geometrie_absente_reste_un_aplat(self):
        """Comportement historique conserve : sans geometrie, on remplit."""
        from hub.maplibre_style_mapper import geometry_to_layer_type
        assert geometry_to_layer_type(None) == ("fill", "fill-color")
        assert geometry_to_layer_type("") == ("fill", "fill-color")


class TestCheminFichier:
    def test_rend_le_chemin_quand_c_en_est_un(self):
        assert chemin_fichier({"geojson_path": "/data/x.geojson"}) == "/data/x.geojson"

    @pytest.mark.parametrize("couche", [
        {"source": {"table": "Batiments"}},
        {"data_url": "https://x/y.geojson"},
        {},
    ])
    def test_rend_None_pour_toute_autre_origine(self, couche):
        """Un appelant qui ne lit que des fichiers doit ignorer ces couches,
        pas croire a un chemin qui n'existe pas."""
        assert chemin_fichier(couche) is None
