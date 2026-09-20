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


# ── Accepter ce que la page d'Onyxia affiche vraiment ────────────────────
#
# Mesure du 2026-09-20 : « Mon compte > Connexion au stockage » ne propose
# aucun jeton d'identite. Elle montre des identifiants deja echanges, dans
# la forme de l'onglet choisi. Exiger un jeton revenait a demander ce que la
# page ne montre pas -- l'utilisateur restait bloque devant le bon ecran.

_CLE = "5EGXMNUXQ6CJ22ACR1V9"
_SECRET = "+EhBgSQvz1y0V++42+Corwbxxre1qYeKbCopEtes"
_JETON = "tete.charge.signature"


def test_la_forme_mc_host_est_reconnue():
    """L'onglet mc colle les trois valeurs dans une URL, separees par `:`."""
    colle = (
        "# Re-run this export when the session token is renewed.\n"
        f"export MC_HOST_default='https://{_CLE}:{_SECRET}:{_JETON}"
        "@minio.lab.sspcloud.fr'\n\nmc ls 'default/mon-seau'\n"
    )
    trouve = s3.extraire_des_identifiants(colle)
    assert trouve["AWS_ACCESS_KEY_ID"] == _CLE
    assert trouve["AWS_SECRET_ACCESS_KEY"] == _SECRET, "le `+` doit survivre"
    assert trouve["AWS_SESSION_TOKEN"] == _JETON


def test_la_forme_export_shell_est_reconnue():
    colle = (
        f'export AWS_ACCESS_KEY_ID="{_CLE}"\n'
        f'export AWS_SECRET_ACCESS_KEY="{_SECRET}"\n'
        f'export AWS_SESSION_TOKEN="{_JETON}"\n'
        'export AWS_S3_ENDPOINT="minio.lab.sspcloud.fr"\n'
        'export AWS_BUCKET_NAME="nic01asfr"\n'
    )
    trouve = s3.extraire_des_identifiants(colle)
    assert trouve["AWS_ACCESS_KEY_ID"] == _CLE
    assert trouve["AWS_SECRET_ACCESS_KEY"] == _SECRET
    assert trouve["AWS_SESSION_TOKEN"] == _JETON
    assert trouve["SSPCLOUD_BUCKET"] == "nic01asfr"


def test_la_forme_fichier_de_configuration_est_reconnue():
    colle = (
        "[default]\n"
        f"aws_access_key_id = {_CLE}\n"
        f"aws_secret_access_key = {_SECRET}\n"
        f"aws_session_token = {_JETON}\n"
    )
    trouve = s3.extraire_des_identifiants(colle)
    assert trouve["AWS_ACCESS_KEY_ID"] == _CLE
    assert trouve["AWS_SESSION_TOKEN"] == _JETON


def test_un_extrait_python_sans_valeurs_ne_donne_rien():
    """L'onglet Python montre parfois le code, pas les identifiants."""
    colle = ('import boto3\n'
             'session = boto3.Session(profile_name="default")\n'
             's3 = session.client("s3", endpoint_url="https://minio.lab.sspcloud.fr")\n')
    assert s3.extraire_des_identifiants(colle) == {}


def test_un_seau_donne_en_exemple_est_ecarte():
    colle = f"AWS_ACCESS_KEY_ID={_CLE}\nbucket = your-bucket\n"
    assert "SSPCLOUD_BUCKET" not in s3.extraire_des_identifiants(colle)


def test_rien_de_colle_est_refuse_sans_appel_reseau():
    r = s3.adopter_ce_qui_est_colle("   ")
    assert r["ok"] is False


def test_du_texte_sans_identifiants_dit_ou_les_prendre():
    r = s3.adopter_ce_qui_est_colle("bonjour, voici mes cles")
    assert r["ok"] is False
    assert "datalab.sspcloud.fr" in r["erreur"]


def test_un_jeton_d_identite_seul_part_vers_le_stockage(monkeypatch):
    """Les deux chemins coexistent : on choisit sur le contenu, pas un bouton."""
    appels = []
    monkeypatch.setattr(s3, "renouveler_les_acces",
                        lambda j, b="": appels.append((j, b)) or {"ok": True})
    s3.adopter_ce_qui_est_colle("tete.charge.signature")
    assert appels == [("tete.charge.signature", "")]


def test_des_identifiants_colles_sont_enregistres(monkeypatch):
    poses = {}
    monkeypatch.setattr(s3, "_poser_le_secret",
                        lambda creds: (poses.update(creds), {"ok": True})[1])
    s3._creds_cache["data"] = {"vieux": "creds"}
    s3._creds_cache["ts"] = time.time()

    r = s3.adopter_ce_qui_est_colle(
        f"export MC_HOST_default='https://{_CLE}:{_SECRET}:"
        f"{_jeton_expirant_dans(86400)}@minio.lab.sspcloud.fr'",
        bucket="nic01asfr",
    )

    assert r["ok"] is True
    assert r["bucket"] == "nic01asfr"
    assert r["permanents"] is False
    assert r["expire_le"], "l'utilisateur doit savoir jusqu'a quand"
    assert poses["AWS_ACCESS_KEY_ID"] == _CLE
    assert poses["AWS_SECRET_ACCESS_KEY"] == _SECRET
    assert s3._creds_cache["data"] is None, "sinon une heure d'anciens acces"


def test_des_identifiants_sans_jeton_sont_dits_permanents(monkeypatch):
    monkeypatch.setattr(s3, "_poser_le_secret", lambda c: {"ok": True})
    r = s3.adopter_ce_qui_est_colle(
        f"aws_access_key_id = {_CLE}\naws_secret_access_key = {_SECRET}\n")
    assert r["ok"] is True and r["permanents"] is True
    assert r["expire_le"] is None


def test_le_message_d_expiration_n_envoie_plus_vers_install_sh():
    """Il y envoyait : le script recopie des identifiants deja morts."""
    assert "install.sh" not in s3._S3_EXPIRED_MESSAGE
    assert "Connexion au stockage" in s3._S3_EXPIRED_MESSAGE
