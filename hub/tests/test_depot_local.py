"""Une publication survit a l'expiration des acces au stockage.

Les identifiants S3 d'un service Onyxia durent sept jours et personne ne les
renouvelle. Jusqu'ici une publication ne vivait que sur MinIO : passe ce
delai, le catalogue se vidait, les livrables disparaissaient et les scenes
repondaient 503. Constate en production le 2026-08-30, puis de nouveau le
2026-09-20 -- deux fois la meme panne, pour la meme raison.

Le hub ecrit desormais sur son propre disque en premier. S3 n'est plus qu'un
second exemplaire : son absence empeche de partager hors du service, plus de
lire.
"""
from __future__ import annotations

import json

import pytest

from hub import depot_local
from hub import s3_publication as s3


@pytest.fixture(autouse=True)
def depot_isole(tmp_path, monkeypatch):
    """Chaque test ecrit dans son propre dossier, jamais dans le vrai depot."""
    monkeypatch.setattr(depot_local, "_PUBLICATIONS", tmp_path / "publications")
    monkeypatch.setattr(depot_local, "_CATALOGUES", tmp_path / "catalogues")
    return tmp_path


class _S3Muet:
    """Un stockage qui refuse tout, comme des identifiants expires."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, erreur=None):
        self.erreur = erreur or RuntimeError("ExpiredToken")
        self.appels: list[str] = []

    def put_object(self, **kw):
        self.appels.append("put")
        raise self.erreur

    def get_object(self, **kw):
        self.appels.append("get")
        raise self.erreur

    def delete_object(self, **kw):
        self.appels.append("delete")
        raise self.erreur

    def head_object(self, **kw):
        self.appels.append("head")
        raise self.erreur


def _stockage_muet(monkeypatch, erreur=None):
    faux = _S3Muet(erreur)
    monkeypatch.setattr(s3, "_get_s3_client",
                        lambda owner="": (faux, "seau", "https://minio.test"))
    return faux


def _stockage_interdit(monkeypatch):
    """Tout appel a S3 fait echouer le test : on veut prouver qu'on lit local."""
    def _interdit(owner=""):
        raise AssertionError("S3 ne devait pas etre interroge")
    monkeypatch.setattr(s3, "_get_s3_client", _interdit)


# ── Publier ──────────────────────────────────────────────────────────────


def test_une_publication_est_ecrite_sur_le_disque_du_hub(monkeypatch):
    poses = {}

    class _S3:
        class exceptions:
            class NoSuchKey(Exception):
                pass

        def put_object(self, **kw):
            poses.update(kw)

    monkeypatch.setattr(s3, "_get_s3_client",
                        lambda owner="": (_S3(), "seau", "https://minio.test"))
    monkeypatch.setattr(s3, "_update_catalog", lambda *a, **k: None)

    info = s3.publish("nic01asfr", "features", "ma-scene", b'{"a":1}',
                      audience="public")

    assert info["local"] is True
    assert info["distant"] is True
    assert depot_local.lire("nic01asfr", "features", "ma-scene",
                            "geojson") == b'{"a":1}'


def test_une_publication_aboutit_meme_si_le_stockage_refuse(monkeypatch):
    """C'etait l'echec le plus couteux : le travail semblait perdu."""
    faux = _stockage_muet(monkeypatch)
    monkeypatch.setattr(s3, "is_s3_credentials_expired", lambda exc: True)
    monkeypatch.setattr(s3, "_update_catalog", lambda *a, **k: None)

    info = s3.publish("nic01asfr", "storymap", "recit", b"<html></html>")

    assert "put" in faux.appels, "on a bien tente S3"
    assert info["local"] is True and info["distant"] is False
    assert "stockage_distant_refuse" in info
    assert info["url"].endswith("/published/nic01asfr/storymap/recit")


def test_sans_depot_local_une_expiration_reste_une_erreur(monkeypatch):
    """Si rien n'a pu etre garde, il faut le dire, pas faire semblant."""
    _stockage_muet(monkeypatch)
    monkeypatch.setattr(s3, "is_s3_credentials_expired", lambda exc: True)
    monkeypatch.setattr(depot_local, "ecrire", lambda *a, **k: False)

    with pytest.raises(RuntimeError) as capture:
        s3.publish("nic01asfr", "storymap", "recit", b"<html></html>")
    assert "expir" in str(capture.value).lower()


def test_un_objet_trop_gros_ne_va_pas_sur_le_disque_du_hub(monkeypatch):
    """Le volume de travail n'est pas fait pour stocker des rasters."""
    monkeypatch.setattr(depot_local, "TAILLE_MAX", 10)
    assert depot_local.ecrire("u", "features", "s", "geojson",
                              b"0123456789012") is False
    assert depot_local.lire("u", "features", "s", "geojson") is None


# ── Lire sans le stockage ────────────────────────────────────────────────


def test_la_lecture_ne_touche_pas_au_stockage_quand_le_disque_repond(monkeypatch):
    depot_local.ecrire("nic01asfr", "features", "ma-scene", "geojson", b"ici")
    _stockage_interdit(monkeypatch)

    assert s3.read("nic01asfr", "features", "ma-scene") == b"ici"


def test_une_plage_d_octets_se_lit_aussi_sur_le_disque(monkeypatch):
    """Les tuiles PMTiles se lisent par plages : sans cela, elles tombent."""
    depot_local.ecrire("u", "features_pmtiles", "t", "pmtiles",
                       b"0123456789")
    _stockage_interdit(monkeypatch)

    bout = s3.read_range("u", "features_pmtiles", "t", "bytes=2-5")

    assert bout["body"] == b"2345"
    assert bout["content_range"] == "bytes 2-5/10"
    assert bout["total_size"] == 10


@pytest.mark.parametrize("plage", ["bytes=8-", "bytes=-2"])
def test_les_formes_de_plage_usuelles_sont_comprises(plage):
    depot_local.ecrire("u", "features_pmtiles", "t", "pmtiles", b"0123456789")
    bout = depot_local.lire_intervalle("u", "features_pmtiles", "t", "pmtiles",
                                       plage, "application/vnd.pmtiles")
    assert bout["body"] == b"89"


@pytest.mark.parametrize("plage", ["", "octets=0-1", "bytes=abc", "bytes=9-3"])
def test_une_plage_incomprehensible_rend_rien_plutot_que_faux(plage):
    depot_local.ecrire("u", "features_pmtiles", "t", "pmtiles", b"0123456789")
    assert depot_local.lire_intervalle("u", "features_pmtiles", "t", "pmtiles",
                                       plage, "x") is None


# ── L'audience, qui commande l'acces ─────────────────────────────────────


def test_l_audience_vient_de_l_index_local(monkeypatch):
    depot_local.ecrire("u", "features", "s", "geojson", b"{}")
    depot_local.ecrire_catalogue("u", [{
        "kind": "features", "slug": "s", "audience": "public",
        "content_type": "application/geo+json", "study_id": "e1",
    }])
    _stockage_interdit(monkeypatch)

    meta = s3.head("u", "features", "s")

    assert meta["metadata"]["audience"] == "public"
    assert meta["metadata"]["study-id"] == "e1"
    assert meta["depuis"] == "local"


def test_une_audience_inconnue_reste_restrictive(monkeypatch):
    """Ne jamais rendre public par defaut : une fuite ne se rattrape pas."""
    depot_local.ecrire("u", "features", "orpheline", "geojson", b"{}")
    _stockage_interdit(monkeypatch)

    meta = s3.head("u", "features", "orpheline")

    assert meta["metadata"]["audience"] == "cerema_internal"


# ── Le catalogue ─────────────────────────────────────────────────────────


def test_le_catalogue_local_passe_devant(monkeypatch):
    depot_local.ecrire_catalogue("u", [{"kind": "features", "slug": "s"}])
    _stockage_interdit(monkeypatch)

    assert s3.get_catalog("u") == [{"kind": "features", "slug": "s"}]


def test_le_catalogue_distant_est_recopie_a_la_premiere_lecture(monkeypatch):
    """Pour que la prochaine expiration ne vide plus rien."""
    attendu = [{"kind": "features", "slug": "s", "audience": "public"}]

    class _S3:
        class exceptions:
            class NoSuchKey(Exception):
                pass

        def get_object(self, **kw):
            class _Corps:
                def read(self_inner):
                    return json.dumps(attendu).encode()
            return {"Body": _Corps()}

    monkeypatch.setattr(s3, "_get_s3_client",
                        lambda owner="": (_S3(), "seau", "https://minio.test"))

    assert s3.get_catalog("u") == attendu
    assert depot_local.catalogue("u") == attendu


def test_un_index_absent_et_un_index_vide_ne_se_confondent_pas():
    assert depot_local.catalogue("personne") is None
    depot_local.ecrire_catalogue("personne", [])
    assert depot_local.catalogue("personne") == []


def test_le_catalogue_s_enregistre_meme_si_le_stockage_refuse(monkeypatch):
    _stockage_muet(monkeypatch)
    s3._save_catalog("u", [{"kind": "features", "slug": "s"}])
    assert depot_local.catalogue("u") == [{"kind": "features", "slug": "s"}]


# ── Depublier ────────────────────────────────────────────────────────────


def test_depublier_retire_les_deux_exemplaires(monkeypatch):
    """Un livrable qui reapparait apres suppression est pire qu'un echec."""
    depot_local.ecrire("u", "features", "s", "geojson", b"{}")
    monkeypatch.setattr(s3, "_remove_from_catalog", lambda *a, **k: None)

    class _S3:
        class exceptions:
            class NoSuchKey(Exception):
                pass

        def delete_object(self, **kw):
            return {}

    monkeypatch.setattr(s3, "_get_s3_client",
                        lambda owner="": (_S3(), "seau", "https://minio.test"))

    assert s3.delete("u", "features", "s") is True
    assert depot_local.lire("u", "features", "s", "geojson") is None


def test_depublier_reussit_si_le_local_a_ete_retire(monkeypatch):
    depot_local.ecrire("u", "features", "s", "geojson", b"{}")
    _stockage_muet(monkeypatch)
    monkeypatch.setattr(s3, "_remove_from_catalog", lambda *a, **k: None)

    assert s3.delete("u", "features", "s") is True
    assert depot_local.lire("u", "features", "s", "geojson") is None


# ── Ne pas sortir du depot ───────────────────────────────────────────────


@pytest.mark.parametrize("mechant", ["../../etc", "..", "a/b", "\\x00"])
def test_aucun_chemin_ne_sort_du_depot(mechant, depot_isole):
    depot_local.ecrire(mechant, "features", mechant, "geojson", b"x")
    for fichier in (depot_isole / "publications").rglob("*"):
        assert depot_isole in fichier.parents or fichier.parent == depot_isole
