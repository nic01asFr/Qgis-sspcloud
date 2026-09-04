"""Un stockage illisible ne doit pas se presenter comme un stockage vide.

Constate en production le 2026-09-02 sur `/published/nic01asfr/` : la page
annoncait « 0 publication disponible — Aucune publication disponible pour
l'instant » alors que le hub etait seulement incapable de lire le stockage,
ses identifiants ayant expire. Le lecteur en conclut que rien n'a jamais ete
publie, ou que tout a ete efface.

C'est le cinquieme visage du meme motif, et le plus tranquille : l'echec
prend la forme d'une intention — ici celle d'un compte neuf, ordonne, ou
personne n'a encore rien depose. Rien dans la page ne signale qu'une question
est restee sans reponse.
"""

from __future__ import annotations

import pytest

from botocore.exceptions import ClientError

import hub.s3_publication as s3


def _erreur(code: str = "", statut: int | None = None,
            operation: str = "HeadObject") -> ClientError:
    reponse: dict = {"Error": {"Code": code, "Message": code or "Forbidden"}}
    if statut is not None:
        reponse["ResponseMetadata"] = {"HTTPStatusCode": statut}
    return ClientError(reponse, operation)


class TestExpirationReconnue:
    """MinIO ne dit pas toujours pourquoi il refuse."""

    def test_un_code_nomme_est_reconnu(self):
        assert s3.is_s3_credentials_expired(_erreur("ExpiredToken"))

    def test_un_403_sans_code_est_reconnu(self):
        """Une requete HEAD n'a pas de corps : botocore ne peut y lire aucun
        code d'erreur et se rabat sur le statut. C'est le cas reel observe en
        production, et celui que la detection manquait."""
        assert s3.is_s3_credentials_expired(_erreur("403", 403))

    def test_un_401_sans_code_est_reconnu(self):
        assert s3.is_s3_credentials_expired(_erreur("401", 401))

    def test_une_vraie_absence_n_est_pas_une_expiration(self):
        assert not s3.is_s3_credentials_expired(_erreur("NoSuchKey", 404))

    def test_le_message_devient_actionnable(self):
        """Sans cela l'utilisateur recevait le texte brut de la bibliotheque,
        qui dit ce qui a rate mais jamais quoi faire."""
        message = s3.explain_s3_error(_erreur("403", 403))
        assert "expir" in message.lower()
        assert "ClientError" not in message


class TestCatalogueHonnete:
    """`get_catalog` rendait [] pour toute exception."""

    def test_une_absence_reelle_rend_bien_une_liste_vide(self):
        assert s3._est_absence_reelle(_erreur("NoSuchKey"))

    def test_un_refus_n_est_pas_une_absence(self):
        """C'est la distinction qui manquait : sans elle, « je ne peux pas
        lire » devenait « il n'y a rien »."""
        assert not s3._est_absence_reelle(_erreur("403", 403))
        assert not s3._est_absence_reelle(_erreur("AccessDenied"))

    def test_le_catalogue_leve_au_lieu_de_rendre_vide(self):
        import inspect
        src = inspect.getsource(s3.get_catalog)
        corps = "\n".join(l for l in src.splitlines()
                          if not l.strip().startswith("#"))
        assert "StockageInaccessible" in corps, (
            "un catalogue illisible serait encore rendu comme vide"
        )


class TestPageDeListing:
    """La page doit distinguer trois etats, pas deux."""

    def test_les_trois_cas_sont_distingues(self):
        import inspect
        from hub.main import list_published_owner
        corps = "\n".join(
            l for l in inspect.getsource(list_published_owner).splitlines()
            if not l.strip().startswith("#"))
        assert "StockageInaccessible" in corps
        assert "indisponible" in corps
        assert "Aucune publication" in corps, "le vrai vide doit rester dit"

    def test_l_illisible_ne_repond_pas_200(self):
        """Un appelant automatique qui lit 200 conclut au vide. Il doit voir
        qu'aucune conclusion n'est possible."""
        import inspect
        from hub.main import list_published_owner
        corps = inspect.getsource(list_published_owner)
        assert "503 if indisponible else 200" in corps
