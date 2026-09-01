"""« Introuvable » et « illisible » sont-ils distingués ?

Constaté en production le 2026-09-01, sept jours après avoir publié une couche
pour l'agent Atlas : l'URL répondait **404**. L'objet existait pourtant. La
vraie cause était l'expiration des identifiants de stockage — MinIO SSPCloud
n'en délivre que de temporaires (politique `stsonly`), sept jours au plus.

`head()` avalait toutes les exceptions et rendait `None` ; son seul appelant en
concluait « absent » et répondait 404. Un lecteur en aurait déduit qu'il avait
mal publié, et serait allé chercher son erreur là où il n'y en avait pas.

C'est encore le motif de la semaine — l'échec qui prend la forme d'une
intention — appliqué à la plus banale des réponses HTTP.

Note pour la suite : le renouvellement automatique par le jeton de
ServiceAccount du pod a été testé et **refusé** par MinIO (« No public key
found for kid ») — il n'accepte que Keycloak comme émetteur. Tant que
l'administration SSPCloud ne déclare pas le cluster, l'expiration est une
contrainte, pas un bug. Reste à la dire clairement.
"""

from __future__ import annotations

import pytest

from hub.s3_publication import StockageInaccessible, _est_absence_reelle


class _Erreur(Exception):
    """Une exception botocore, réduite à ce qui nous intéresse."""

    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class TestDistinction:
    @pytest.mark.parametrize("code", ["404", "NoSuchKey", "NoSuchBucket", "NotFound"])
    def test_le_stockage_dit_que_l_objet_n_existe_pas(self, code):
        assert _est_absence_reelle(_Erreur(code)) is True

    @pytest.mark.parametrize("code", [
        "ExpiredToken", "ExpiredTokenException", "InvalidAccessKeyId",
        "AccessDenied", "SignatureDoesNotMatch", "InvalidToken",
    ])
    def test_tout_le_reste_est_une_impossibilite_de_lire(self, code):
        """Identifiants périmés, refus, signature invalide : l'objet peut
        exister parfaitement. Répondre « introuvable » enverrait le lecteur
        chercher une erreur qu'il n'a pas commise."""
        assert _est_absence_reelle(_Erreur(code)) is False

    def test_une_panne_sans_code_n_est_pas_une_absence(self):
        """Réseau coupé, délai dépassé : on ne sait pas, donc on ne prétend
        pas que l'objet manque."""
        assert _est_absence_reelle(Exception("connexion interrompue")) is False


class TestReponseAuLecteur:
    def test_l_appelant_distingue_les_deux_cas(self):
        import inspect

        import hub.main as main
        src = inspect.getsource(main.serve_published)
        assert "StockageInaccessible" in src, (
            "l'appelant traite encore toute erreur comme une absence"
        )
        i = src.find("StockageInaccessible")
        assert "503" in src[i:i + 400], (
            "une impossibilité de lire doit répondre 503, pas 404 : l'objet "
            "existe peut-être"
        )

    def test_le_message_est_actionnable(self):
        """Le lecteur doit savoir quoi faire, pas seulement que ça a raté.

        On lit le message rendu, pas le code qui le rend : il vit dans une
        constante, et vérifier la fonction ne prouvait rien.
        """
        from hub.s3_publication import _S3_EXPIRED_MESSAGE as msg
        assert "expir" in msg.lower(), "la cause n'est pas nommée"
        assert "install.sh" in msg, "le remède n'est pas donné"
        assert "7 jours" in msg, "la durée n'est pas dite, donc pas anticipable"

    def test_une_erreur_quelconque_reste_lisible(self):
        """Un message technique brut vaut mieux qu'un silence, tant qu'il
        nomme l'erreur."""
        from hub.s3_publication import explain_s3_error
        texte = explain_s3_error(Exception("connexion interrompue"))
        assert "connexion interrompue" in texte

    def test_l_exception_porte_un_message_lisible(self):
        e = StockageInaccessible("Les accès au stockage ont expiré.")
        assert "expir" in str(e)
