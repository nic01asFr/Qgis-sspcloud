"""Une couche dit-elle assez pour être stylée sans être lue ?

Suite du principe posé avec l'agent Atlas : si un runtime doit fonctionner sans
détenir les entités, tout ce qu'il en dérivait doit être déclarable.

Il a donné les lignes exactes. `declarative-style.js:236` fait
`Math.min(...nums)` / `Math.max(...nums)` sur les valeurs des entités, et deux
`new Set()` collectent les modalités pour la catégorisation : une symbologie
graduée ou catégorisée est donc **impossible sans lire toute la couche**.
`pointFallbackZoom` échantillonne une entité sur quarante et calcule leur empan
moyen en mètres pour décider d'un repli en points.

Deux déclarations suffisent à lever les deux : `attribute_stats` et
`span_moyen_m`. Elles se calculent là où les entités sont déjà sous la main —
le parcours est gratuit.

Ce qui n'est **pas** ici, et volontairement : `resolveFeatureProps` se résout
par `queryRenderedFeatures` côté rendu, et `centroidCollection` a besoin de la
géométrie de chaque entité — aucune statistique n'y suppléerait. Les tuiles y
répondent mieux qu'une déclaration.
"""

from __future__ import annotations

import math

import pytest

import hub.studies as studies


def _stats(features):
    """Le calcul tel qu'il tourne dans le pod."""
    st = {}
    for f in features:
        for k, v in (f.get("properties") or {}).items():
            s = st.setdefault(k, {"n": 0, "min": None, "max": None,
                                  "valeurs": set(), "trop": False})
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                if len(s["valeurs"]) < 50:
                    s["valeurs"].add(str(v)[:80])
                else:
                    s["trop"] = True
            else:
                s["min"] = v if s["min"] is None else min(s["min"], v)
                s["max"] = v if s["max"] is None else max(s["max"], v)
            s["n"] += 1
    out = {}
    for k, s in st.items():
        d = {"n_renseignes": s["n"]}
        if s["min"] is not None:
            d["min"], d["max"] = s["min"], s["max"]
        if s["valeurs"]:
            d["valeurs_distinctes"] = "plus de 50" if s["trop"] else sorted(s["valeurs"])
        out[k] = d
    return out


def _empan(features):
    emp = []
    for f in features[::max(1, len(features) // 200)]:
        xs, ys = [], []

        def pts(c):
            if not isinstance(c, (list, tuple)) or not c:
                return
            if isinstance(c[0], (int, float)) and len(c) >= 2:
                xs.append(c[0]); ys.append(c[1])
            else:
                for s in c:
                    pts(s)

        pts((f.get("geometry") or {}).get("coordinates"))
        if len(xs) < 2:
            continue
        lat = sum(ys) / len(ys)
        dx = (max(xs) - min(xs)) * 111320 * math.cos(math.radians(lat))
        dy = (max(ys) - min(ys)) * 111320
        emp.append(math.hypot(dx, dy))
    if not emp:
        return None
    emp.sort()
    return round(emp[len(emp) // 2], 1)


def _batiments(n=300):
    """Des bâtiments façon BD TOPO, à Marseille, d'environ 20 m de côté."""
    return [{
        "geometry": {"type": "Polygon", "coordinates": [[
            [5.40 + i * 1e-4, 43.30], [5.4002 + i * 1e-4, 43.30],
            [5.4002 + i * 1e-4, 43.3002], [5.40 + i * 1e-4, 43.30]]]},
        "properties": {"hauteur": 6 + i % 20,
                       "nature": ["Indifferenciee", "Industriel", "Religieux"][i % 3]},
    } for i in range(n)]


class TestStatistiquesDAttributs:
    def test_un_champ_numerique_donne_de_quoi_graduer(self):
        s = _stats(_batiments())["hauteur"]
        assert s["min"] == 6 and s["max"] == 25

    def test_un_champ_textuel_donne_de_quoi_categoriser(self):
        s = _stats(_batiments())["nature"]
        assert s["valeurs_distinctes"] == ["Indifferenciee", "Industriel", "Religieux"]

    def test_un_champ_a_forte_cardinalite_dit_qu_il_y_en_a_trop(self):
        """Livrer une liste tronquée ferait croire à un inventaire complet :
        un consommateur catégoriserait sur 50 modalités en en ignorant 150."""
        f = [{"geometry": {"type": "Point", "coordinates": [5.4, 43.3]},
              "properties": {"id_unique": f"ID-{i:04d}"}} for i in range(200)]
        assert _stats(f)["id_unique"]["valeurs_distinctes"] == "plus de 50"

    def test_un_booleen_est_une_modalite_pas_un_nombre(self):
        """En Python, `True` est un entier. Graduer sur vrai/faux n'a aucun
        sens ; les catégoriser en a."""
        f = [{"properties": {"actif": True}}, {"properties": {"actif": False}}]
        s = _stats(f)["actif"]
        assert "min" not in s
        assert set(s["valeurs_distinctes"]) == {"True", "False"}

    def test_seules_les_valeurs_renseignees_sont_comptees(self):
        f = [{"properties": {"h": 10}}, {"properties": {}}, {"properties": {"h": 20}}]
        assert _stats(f)["h"]["n_renseignes"] == 2

    def test_une_couche_sans_attributs_ne_produit_rien(self):
        assert _stats([{"geometry": {"type": "Point", "coordinates": [5, 43]}}]) == {}


class TestEmpanDesGeometries:
    def test_l_ordre_de_grandeur_est_juste(self):
        """Des bâtiments de ~20 m de côté : la diagonale vaut ~28 m. On vise
        un ordre de grandeur, pas une mesure géodésique."""
        e = _empan(_batiments())
        assert 20 < e < 40, f"empan aberrant : {e} m"

    def test_une_couche_de_points_ne_donne_pas_d_empan(self):
        """Un point n'a pas d'étendue : rendre 0 laisserait croire à des objets
        minuscules et déclencherait un repli inutile."""
        assert _empan([{"geometry": {"type": "Point", "coordinates": [5.4, 43.3]}}]) is None

    def test_aucune_entite_ne_donne_rien(self):
        assert _empan([]) is None

    def test_la_longitude_est_corrigee_par_la_latitude(self):
        """Un degré de longitude vaut ~111 km à l'équateur et ~74 km à Paris.
        Sans le cosinus, une couche du nord paraîtrait bien plus vaste."""
        def carre(lat):
            return [{"geometry": {"type": "Polygon", "coordinates":
                     [[[0, lat], [0.01, lat], [0.01, lat + 0.01], [0, lat]]]}}]
        assert _empan(carre(0)) > _empan(carre(60)), \
            "la correction de longitude ne s'applique pas"


class TestBranchement:
    def test_les_statistiques_sont_posees_sur_la_couche(self):
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert 'layer_entry["attribute_stats"]' in code
        assert 'layer_entry["span_moyen_m"]' in code

    def test_un_echec_de_calcul_n_interrompt_pas_le_build(self):
        """Une couche sans statistiques reste une couche."""
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert "SCENE_MANIFEST_STATS_ERR" in code
        assert "SCENE_MANIFEST_SPAN_ERR" in code

    def test_l_echantillonnage_borne_le_cout(self):
        """14 270 entités ne doivent pas coûter 14 270 calculs d'empan."""
        code = studies.build_scene_manifest_from_qgis_pod_code("abc", "def")
        assert "features[::max(1, len(features) // 200)]" in code
