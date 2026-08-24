"""Une valeur absente reste-t-elle absente ?

Relevé par l'agent Atlas le 2026-08-24, sur la 400e entité d'une couche que
nous lui avions livrée : elle portait la chaîne `"NULL"` comme hauteur, et sa
symbologie la peignait dans la classe la plus basse. **Une donnée absente
déguisée en mesure** — la carte s'affiche, complète, colorée, et fausse.

D'où cela venait. Un NULL de QGIS est un QVariant vide : ni `None`, ni `int`,
ni `str`. Il traversait donc tous les tests et finissait dans `str(v)`, qui
rend `"NULL"`. Et symétriquement, une date invalide écrivait `None`, donc
`null` en JSON — deux façons différentes de dire « absent », aucune correcte,
et toutes deux lisibles comme des valeurs.

La règle : **une valeur absente est absente du dictionnaire.** Ni chaîne, ni
`null`. Un consommateur qui ne trouve pas la clé sait qu'il n'y a rien ; un
consommateur qui trouve `"NULL"` ou `0` croit avoir une mesure.

C'est le même motif que les quatre autres — l'échec qui prend la forme d'une
intention — sous la forme qu'Atlas a nommée : ici il prend la forme d'une
**valeur**.
"""

from __future__ import annotations

import hub.studies as studies


def _code() -> str:
    return studies.build_scene_manifest_from_qgis_pod_code("abc", "def")


class TestOmissionDesValeursAbsentes:
    def test_le_null_qgis_est_reconnu(self):
        """Il faut l'importer pour pouvoir le comparer : sans lui, un QVariant
        vide passe tous les tests de type."""
        c = _code()
        assert "from qgis.core import NULL as _NULL" in c
        assert "if v == _NULL:" in c

    def test_la_chaine_NULL_ne_peut_plus_sortir(self):
        """Le dernier recours `str(v)` était la porte d'entrée du bug."""
        c = _code()
        assert '_t not in ("NULL", "None", "")' in c, (
            "un type inconnu peut encore sortir sous forme de chaîne « NULL »"
        )

    def test_une_date_invalide_est_omise_et_non_mise_a_null(self):
        """Écrire None produisait un `null` en JSON — une réponse, pas une
        absence."""
        c = _code()
        assert "v.year() if v.isValid() else None" not in c, (
            "une date invalide écrit encore null"
        )
        assert "if v.isValid():" in c

    def test_l_import_du_null_ne_casse_pas_hors_qgis(self):
        """Le code tourne dans le pod ; s'il est exécuté ailleurs, l'import
        échoue et ne doit pas interrompre la construction."""
        c = _code()
        i = c.find("from qgis.core import NULL")
        assert "try:" in c[max(0, i - 120):i]
        assert "_NULL = None" in c

    def test_la_comparaison_au_null_ne_peut_pas_lever(self):
        """`v == _NULL` lève sur certains types Qt : l'entité entière serait
        perdue pour un attribut."""
        c = _code()
        i = c.find("if v == _NULL:")
        assert "try:" in c[max(0, i - 100):i]
        assert "except Exception:" in c[i:i + 200]


class TestRegleGenerale:
    def test_aucune_valeur_sentinelle_n_est_ecrite(self):
        """Ni chaîne magique, ni zéro de remplacement : ce sont eux qui se
        font passer pour des mesures."""
        c = _code()
        for sentinelle in ('props[k] = "NULL"', "props[k] = None", "props[k] = 0"):
            assert sentinelle not in c, f"valeur sentinelle écrite : {sentinelle}"

    def test_les_omissions_utilisent_continue(self):
        """Une clé absente, pas une clé à valeur vide."""
        c = _code()
        i = c.find("for k in feat.fields().names():")
        bloc = c[i:i + 2200]
        assert bloc.count("continue") >= 4, (
            "les cas d'absence n'omettent pas tous la clé"
        )
