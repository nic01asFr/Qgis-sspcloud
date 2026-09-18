"""Le service renouvelle ses acces au stockage, au lieu d'attendre un script.

Les identifiants S3 d'un service Onyxia durent sept jours et personne ne les
renouvelle. Passe ce delai, le catalogue se vide, les livrables disparaissent
et les scenes publiees repondent 503 -- sans que le code soit en cause.

Mesure du 2026-09-18 : le service Jupyter de reference datait du 23 aout.
Relancer `install.sh` n'y changeait RIEN -- le script recopie fidelement les
identifiants du pod jupyter, morts avec lui (empreintes identiques verifiees
des deux cotes). Le message d'erreur envoyait pourtant l'utilisateur
exactement la, dans une impasse.
"""
from __future__ import annotations

import base64
import json
import time

import pytest

from hub import s3_publication as s3


# ── Ce qu'on refuse avant meme d'appeler le stockage ─────────────────────


def test_un_jeton_absent_est_refuse_sans_appel_reseau():
    r = s3.renouveler_les_acces("")
    assert r["ok"] is False
    assert "Aucun jeton" in r["erreur"]


def test_ce_qui_n_est_pas_un_jeton_est_refuse_avec_le_geste():
    r = s3.renouveler_les_acces("ma-cle-minio")
    assert r["ok"] is False
    assert "datalab.sspcloud.fr" in r["erreur"], "il faut dire ou le prendre"


# ── L'echange avec le stockage ───────────────────────────────────────────


def _faux_jeton() -> str:
    return "a.b.c"


def test_un_jeton_refuse_par_le_stockage_est_explique(monkeypatch):
    import urllib.error

    def _refus(*a, **k):
        raise urllib.error.HTTPError("url", 403, "Forbidden", {}, None)

    monkeypatch.setattr(s3.urllib.request, "urlopen", _refus)
    r = s3.renouveler_les_acces(_faux_jeton())
    assert r["ok"] is False
    assert "expire" in r["erreur"] or "refuse" in r["erreur"]


def test_un_stockage_injoignable_ne_fait_pas_tomber_le_service(monkeypatch):
    def _explose(*a, **k):
        raise OSError("reseau coupe")

    monkeypatch.setattr(s3.urllib.request, "urlopen", _explose)
    r = s3.renouveler_les_acces(_faux_jeton())
    assert r["ok"] is False
    assert "injoignable" in r["erreur"]


def test_les_acces_obtenus_sont_enregistres(monkeypatch):
    xml = ("<Response><AssumeRoleWithWebIdentityResult><Credentials>"
           "<AccessKeyId>AKI</AccessKeyId>"
           "<SecretAccessKey>SEC</SecretAccessKey>"
           "<SessionToken>JETON</SessionToken>"
           "<Expiration>2026-09-26T00:00:00Z</Expiration>"
           "</Credentials></AssumeRoleWithWebIdentityResult></Response>")

    class _Rep:
        def read(self):
            return xml.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    poses = {}
    monkeypatch.setattr(s3.urllib.request, "urlopen", lambda *a, **k: _Rep())
    monkeypatch.setattr(s3, "_poser_le_secret",
                        lambda creds: (poses.update(creds), {"ok": True})[1])

    r = s3.renouveler_les_acces(_faux_jeton(), bucket="mon-seau")

    assert r["ok"] is True
    assert poses["AWS_ACCESS_KEY_ID"] == "AKI"
    assert poses["AWS_SESSION_TOKEN"] == "JETON"
    assert poses["SSPCLOUD_BUCKET"] == "mon-seau"


def test_le_cache_est_vide_apres_renouvellement(monkeypatch):
    """Sinon le service continuerait une heure avec les anciens acces."""
    class _Rep:
        def read(self):
            return (b"<Credentials><AccessKeyId>A</AccessKeyId>"
                    b"<SecretAccessKey>S</SecretAccessKey>"
                    b"<SessionToken>T</SessionToken></Credentials>")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(s3.urllib.request, "urlopen", lambda *a, **k: _Rep())
    monkeypatch.setattr(s3, "_poser_le_secret", lambda c: {"ok": True})
    s3._creds_cache["data"] = {"vieux": "creds"}
    s3._creds_cache["ts"] = time.time()

    s3.renouveler_les_acces(_faux_jeton())

    assert s3._creds_cache["data"] is None


# ── Dire l'echeance, au lieu de laisser decouvrir la panne ───────────────


def _jeton_expirant_dans(secondes: float) -> str:
    charge = base64.urlsafe_b64encode(
        json.dumps({"exp": time.time() + secondes}).encode()).decode().rstrip("=")
    return "tete." + charge + ".signature"


def test_des_acces_encore_valides_sont_dits_valides(monkeypatch):
    monkeypatch.setattr(s3, "_read_passerelle_s3_creds",
                        lambda *a, **k: {"AWS_SESSION_TOKEN": _jeton_expirant_dans(86400)})
    etat = s3.etat_des_acces()
    assert etat["valides"] is True
    assert etat["expire_dans_heures"] == pytest.approx(24, abs=0.2)


def test_des_acces_perimes_sont_dits_perimes(monkeypatch):
    monkeypatch.setattr(s3, "_read_passerelle_s3_creds",
                        lambda *a, **k: {"AWS_SESSION_TOKEN": _jeton_expirant_dans(-3600)})
    assert s3.etat_des_acces()["valides"] is False


def test_des_acces_permanents_ne_perimen_jamais(monkeypatch):
    """Une cle de service n'a pas de jeton de session : rien n'expire."""
    monkeypatch.setattr(s3, "_read_passerelle_s3_creds",
                        lambda *a, **k: {"AWS_ACCESS_KEY_ID": "AKI"})
    etat = s3.etat_des_acces()
    assert etat["valides"] is True and etat["permanents"] is True


def test_un_stockage_illisible_est_dit_invalide_pas_valide(monkeypatch):
    """Dans le doute on annonce la panne : c'est ce qui se passe vraiment."""
    def _explose(*a, **k):
        raise RuntimeError("Creds S3 indisponibles")

    monkeypatch.setattr(s3, "_read_passerelle_s3_creds", _explose)
    assert s3.etat_des_acces()["valides"] is False
