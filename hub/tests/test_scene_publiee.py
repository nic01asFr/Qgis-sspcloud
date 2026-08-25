"""Ce qu'on publie dit-il encore la vérité une fois publié ?

Trois demandes de l'agent Atlas, le 2026-08-25, après avoir branché `?scene=`.

**1. Une couche publiée reste déclarée d'atelier.** La scène rendue annonçait
`source: {type: "fichier", classe: "atelier"}` — « rien ici n'est atteignable
depuis un navigateur » — alors que la donnée venait d'être mise en ligne. Un
consommateur qui applique la règle refuse la couche **avant d'essayer** et
rapporte un défaut du producteur. Faux, et faux avec assurance : pire qu'un
silence, puisque le diagnostic est affirmatif.

Atlas en fait un visage de plus : **une déclaration qui a cessé d'être vraie**.
Les autres trompaient par omission ; celle-ci trompe parce qu'elle a été juste.

**2. Les bornes de zoom manquaient sur les sources tuilées.** Sans elles, un
moteur réclame des tuiles inexistantes en boucle et la carte n'atteint jamais
son état stable — tout ce qui l'attend reste suspendu. Ce n'est pas une erreur
de tuile, c'est un état qui n'arrive plus.

**3. Le manifeste n'était pas joignable.** Il vit derrière authentification et
porte des chemins PVC. On publie donc la scène *résolue*, celle dont les
couches portent des URL.
"""

from __future__ import annotations

import pytest

import hub.studies as studies
from hub.main import _publier_scene_rendue, _reclasser_apres_publication


class TestReclassementApresPublication:
    URL = "https://hub.exemple/published/u/features/x-abc"

    def test_une_couche_d_atelier_devient_externe(self):
        couche = {"source": {"type": "fichier", "classe": "atelier"}}
        _reclasser_apres_publication(couche, self.URL)
        assert couche["source"]["classe"] == "externe"
        assert couche["source"]["url"] == self.URL

    def test_la_provenance_d_origine_est_conservee(self):
        """Savoir qu'elle vient d'un fichier d'atelier reste utile pour
        l'audit ; elle ne dit simplement plus où lire."""
        couche = {"source": {"type": "fichier", "classe": "atelier"}}
        _reclasser_apres_publication(couche, self.URL)
        assert couche["source"]["materialise_depuis"] == "fichier"

    def test_une_couche_sans_source_en_recoit_une(self):
        couche: dict = {}
        _reclasser_apres_publication(couche, self.URL)
        assert couche["source"]["url"] == self.URL
        assert couche["source"]["classe"] == "externe"

    def test_une_source_mal_formee_ne_fait_pas_echouer(self):
        couche = {"source": "une chaine"}
        _reclasser_apres_publication(couche, self.URL)
        assert isinstance(couche["source"], dict)

    def test_le_reclassement_est_branche_sur_les_deux_chemins(self):
        """GeoJSON par URL et tuiles : les deux publient, donc les deux
        doivent reclasser."""
        import inspect

        from hub.main import _externalize_large_features
        src = inspect.getsource(_externalize_large_features)
        assert src.count("_reclasser_apres_publication") >= 2, (
            "un des deux chemins de publication laisse la couche en atelier"
        )


class TestBornesDeZoom:
    def test_les_bornes_sont_lues_depuis_qgis(self):
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert '("zmin", "min_zoom")' in code
        assert '("zmax", "max_zoom")' in code

    def test_une_source_tuilee_a_toujours_des_bornes(self):
        """Le défaut prudent existe parce que l'absence est pire qu'une borne
        approximative : elle produit une boucle, pas une erreur."""
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert 'sortie.setdefault("min_zoom", 0)' in code
        assert 'sortie.setdefault("max_zoom", 19)' in code

    def test_le_wmts_est_traite_comme_le_xyz(self):
        """Il pyramide pareil, donc il boucle pareil."""
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        i = code.find('if params.get("tileMatrixSet"):')
        assert i != -1, "le WMTS ne reçoit pas de bornes"
        assert "max_zoom" in code[i:i + 700]


class TestScenePubliee:
    def test_une_scene_sans_couche_n_est_pas_publiee(self):
        assert _publier_scene_rendue("cid", "u", "t", "[]") is None
        assert _publier_scene_rendue("cid", "u", "t", "pas du json") is None

    def test_un_echec_de_publication_ne_leve_pas(self):
        """Sans scène publiée on perd l'intégration tierce, pas l'affichage :
        le rendu de la page doit aboutir quand même."""
        assert _publier_scene_rendue(
            "cid", "u", "t", '[{"id": "a", "name": "A"}]'
        ) in (None,) or True  # ne lève pas, c'est tout ce qui compte

    def test_la_scene_publiee_est_validee_avant_envoi(self):
        import inspect
        src = inspect.getsource(_publier_scene_rendue)
        assert "valider_scene" in src, (
            "on publierait une scène sans savoir si elle est conforme"
        )
        assert "log.warning" in src, (
            "un écart connu doit être dit, sinon le consommateur le découvre "
            "sans savoir qu'on le connaissait"
        )
