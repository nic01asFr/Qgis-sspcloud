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


class TestProvenanceProjet:
    """`source` designait deux choses opposees selon le niveau.

    A la racine, la provenance du projet ; au niveau d'une couche, l'origine
    des donnees -- la ou les runtimes vont chercher quoi lire. Le jour ou
    `layer.source` devient une union discriminee d'origine, un meme document
    aurait porte les deux sens. On a renomme la racine en `provenance` pendant
    que le champ n'etait encore lu nulle part.
    """

    def test_la_nouvelle_graphie_est_lue(self):
        from hub.scene_layers import provenance_projet
        m = {"provenance": {"producer": "qgis-sspcloud/hub", "study_id": "abc"}}
        assert provenance_projet(m)["study_id"] == "abc"

    def test_les_manifests_deja_ecrits_restent_lisibles(self):
        """Ceux du PVC portent encore `source` -- aucune migration a faire."""
        from hub.scene_layers import provenance_projet
        m = {"source": {"project_qgs": "/data/x.qgz", "study_id": "abc"}}
        assert provenance_projet(m)["study_id"] == "abc"

    def test_la_nouvelle_graphie_prime(self):
        from hub.scene_layers import provenance_projet
        m = {"provenance": {"study_id": "neuf"}, "source": {"study_id": "vieux"}}
        assert provenance_projet(m)["study_id"] == "neuf"

    def test_sans_rien_on_rend_un_dictionnaire_vide(self):
        from hub.scene_layers import provenance_projet
        assert provenance_projet({}) == {}

    def test_une_source_de_couche_n_est_pas_une_provenance(self):
        """Le piege qu'on ferme : une origine de donnee ne doit jamais etre
        prise pour la provenance du projet."""
        from hub.scene_layers import origine_donnees, provenance_projet
        couche = {"source": {"table": "Batiments"}}
        assert origine_donnees(couche) == ("table", "Batiments")
        # provenance_projet ne s'applique pas a une couche : on ne l'appelle
        # jamais dessus. Le test documente la frontiere.
        assert provenance_projet({"provenance": {"study_id": "abc"}})["study_id"] == "abc"


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
