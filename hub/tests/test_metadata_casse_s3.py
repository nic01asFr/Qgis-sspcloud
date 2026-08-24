"""Une publication déclarée publique est-elle vraiment accessible ?

Bug de production trouvé le 2026-08-24, en publiant une couche pour l'agent
Atlas. `publish()` pose la métadonnée `audience`, MinIO la rend `Audience` — il
traite les métadonnées comme des en-têtes HTTP, où la casse ne signifie rien.

Le gate de `/published` lisait `.get("audience")` en minuscules, ne trouvait
rien, et appliquait le défaut restrictif. Conséquence : **toute publication
déclarée publique répondait 401**. Le pire cas pour un contrôle d'accès — une
clé absente est interprétée comme un refus, donc le défaut sûr masque le bug.

Constaté en vrai : métadonnées `{'Audience': 'public', ...}` et pourtant
`meta.get('audience')` → absente, URL → 401.
"""

from __future__ import annotations

import pytest

from hub.s3_publication import _metadata_insensible_casse as normaliser


class TestLectureDesMetadonnees:
    def test_la_cle_capitalisee_se_lit_en_minuscules(self):
        """Le cas exact rencontré : S3 rend `Audience`, on interroge
        `audience`."""
        m = normaliser({"Audience": "public", "Kind": "features"})
        assert m.get("audience") == "public"

    def test_les_cles_d_origine_sont_conservees(self):
        """Qui inspecte le dictionnaire doit voir ce que S3 a réellement
        rendu -- on ajoute, on ne remplace pas."""
        m = normaliser({"Audience": "public", "Published-At": "1787605112"})
        assert m["Audience"] == "public"
        assert m["Published-At"] == "1787605112"

    def test_une_cle_deja_minuscule_n_est_pas_ecrasee(self):
        """Si les deux graphies coexistent, celle d'origine gagne : on ne
        devine pas laquelle des deux fait foi."""
        m = normaliser({"audience": "restricted", "Audience": "public"})
        assert m["audience"] == "restricted"

    @pytest.mark.parametrize("brut", [{}, None, [], "texte"])
    def test_une_entree_inattendue_ne_fait_pas_echouer(self, brut):
        """`head()` sert un contrôle d'accès : il ne doit jamais lever."""
        assert normaliser(brut) == {} or isinstance(normaliser(brut), dict)

    def test_les_metadonnees_reelles_d_une_publication(self):
        """Relevé sur l'objet publié pour la scène de Sète."""
        m = normaliser({
            "Audience": "public", "Kind": "features", "Owner": "nic01asfr",
            "Published-At": "1787605112",
            "Slug": "d48616a3d824-b-timents__bd_to-ed3abcd5",
        })
        assert m.get("audience") == "public", "le gate refuserait cette publication"
        assert m.get("owner") == "nic01asfr"
        assert m.get("kind") == "features"


def test_le_lecteur_est_bien_branche_sur_head():
    """La normalisation ne sert que si `head()` s'en sert : c'est lui que le
    gate appelle."""
    import inspect

    from hub import s3_publication
    src = inspect.getsource(s3_publication.head)
    assert "_metadata_insensible_casse" in src, (
        "head() rend les métadonnées brutes : le gate retombera sur le défaut"
    )
