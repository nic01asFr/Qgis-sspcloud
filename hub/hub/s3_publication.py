"""
hub.s3_publication — Publication des livrables user vers S3 MinIO SSPCloud.

Phase 3 du refactor (post 2026-05-15). Met en œuvre le principe :

  S3 user = espace de publication (durable, public-read).
  PVC workspace = atelier privé.

L'user publie un livrable produit dans son workspace (HTML storymap, projet
QGIS, GeoPackage exporté, recette) → le hub lit le fichier depuis le pod
workspace et le pousse vers `s3://{bucket}/qgis-workspace/published/`.

Le hub utilise les credentials `passerelle-s3-creds` (longue durée, maintenus
par passerelle) — l'user n'expose pas ses creds personnels. La donnée vit
sous son nom dans son bucket personnel SSPCloud.

Format S3 :
  s3://{bucket}/qgis-workspace/published/{owner}/{kind}/{slug}.{ext}

URL publique (ACL public-read posée à l'upload) :
  https://minio.lab.sspcloud.fr/{bucket}/qgis-workspace/published/{owner}/{kind}/{slug}.{ext}

Catalogue : le hub maintient `s3://{bucket}/qgis-workspace/catalog/{owner}.json`
avec l'index des publications (slug, kind, date, taille, URL).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Any

log = logging.getLogger("hub.s3_publication")

_KINDS = {
    "storymap", "flux", "recipe", "dataset", "pdf",
    # Sprint Composants Phase 3 (2026-06-26) : strate ASSEMBLAGES.
    # Un assembly publie = un livrable HTML composite (storymap_dsfr,
    # dashboard, sheet_a4, modal_embed, atlas_immersive). Distinct du
    # legacy "storymap" qui est storymap_dsfr.py Leaflet.
    "assembly",
    # Sprint Composants Phase 3 : strate COMPOSANTS (composant publishable
    # standalone, ex: interactive_map iframe-embeddable site tiers).
    "component",
    # Sprint sec-vague0 dette OOM piste 1a (2026-07-20) : strate FEATURES.
    # Complete le contract V0.3.2 pivot universel (data_url + geojson_url
    # existants dans ComponentSource + commentaire main.py:5792 "laisse
    # le client fetch (URL publique)" - jamais implemente). Un geojson
    # externalise = livrable atomique data-only reference par un assembly
    # via layer.geojson=URL_string. MapLibre native accepte URL string
    # dans addSource(...data). Reduit HTML publish 38MB -> ~500KB pour
    # les assemblies avec beaucoup de features, contourne le blocage
    # GIL sur Jinja2 render.
    "features",
    # Sprint sec-vague0 dette OOM piste PMTiles V0.4 (2026-07-21) : strate
    # FEATURES_PMTILES. Vector tiles pmtiles au lieu de geojson brut.
    # Bypass la limite MinIO SSPCloud stsonly (put_object echoue >5MB) en
    # produisant un fichier compact (~2MB pour 14270 features BD TOPO)
    # via encoding MVT + compression zstd/gzip interne pmtiles v3.
    # MapLibre lit via HTTP Range Requests (16KB chunks), Cache-Control
    # immuable (URL avec hash). Le kind separe de "features" permet un
    # servi different cote serve_published (Range + no gzip re-encoding).
    "features_pmtiles",
}
_KIND_EXT = {
    "storymap":  "html",
    "flux":      "qgz",
    "recipe":    "yaml",
    "dataset":   "gpkg",
    "pdf":       "pdf",
    "assembly":  "html",
    "component": "html",
    "features":  "geojson",
    "features_pmtiles": "pmtiles",
}
_KIND_CONTENT_TYPE = {
    "storymap":  "text/html; charset=utf-8",
    "flux":      "application/octet-stream",
    "recipe":    "application/x-yaml; charset=utf-8",
    "dataset":   "application/geopackage+sqlite3",
    "pdf":       "application/pdf",
    "assembly":  "text/html; charset=utf-8",
    "features":  "application/geo+json; charset=utf-8",
    "features_pmtiles": "application/vnd.pmtiles",
    "component": "text/html; charset=utf-8",
}

# Extensions éventuellement collées au slug (clé S3). L'URL hub canonique
# /published/{owner}/{kind}/{slug} n'en porte pas (serve_published les strip).
_SLUG_EXTS = (
    ".html", ".pdf", ".yaml", ".json", ".gpkg", ".qgz",
    ".zip", ".pmtiles", ".geojson",
)


def hub_url_for(owner: str, kind: str, slug: str, hub_base: str) -> str:
    """URL partageable hub, calculée à la lecture (ne pas persister dans S3)."""
    base = (hub_base or "").rstrip("/")
    clean = (slug or "").split("?")[0]
    for ext in _SLUG_EXTS:
        if clean.endswith(ext):
            clean = clean[: -len(ext)]
            break
    return f"{base}/published/{owner}/{kind}/{clean}"


def enrich_catalog_item(item: dict, hub_base: str) -> dict:
    """Ajoute hub_url sans toucher au locateur MinIO `url`. Copie superficielle."""
    out = dict(item or {})
    owner = out.get("owner") or ""
    kind = out.get("kind") or ""
    slug = out.get("slug") or ""
    if not (owner and kind and slug and hub_base):
        return out
    out.setdefault("hub_url", hub_url_for(owner, kind, slug, hub_base))
    return out


# Chemin du secret passerelle (creds long-lived service-side)
_SECRET_NAME = os.getenv("PASSERELLE_S3_SECRET", "passerelle-s3-creds")
_S3_PREFIX = "qgis-workspace/published"
_CATALOG_PREFIX = "qgis-workspace/catalog"


# ── Lecture credentials passerelle (cached, refresh hourly) ──────────────────

_creds_cache: dict[str, Any] = {"ts": 0, "data": None}
_CREDS_TTL = 3600  # re-lit le secret toutes les heures (token STS rotation)

# Marge avant expiration : un jeton valable moins longtemps que ca est traite
# comme perime, sinon une publication demarree juste avant l'echeance echoue
# en cours de route.
_STS_MARGE_S = 120


def _sts_encore_valide(creds: dict[str, str]) -> bool:
    """Vrai si le jeton de session porte une echeance encore dans le futur.

    Les jetons S3 de SSPCloud sont des JWT dont le champ `exp` porte la date
    d'expiration. Un jeton sans `exp` lisible (creds long-lived, format
    inattendu) est considere comme valide : on ne rejette que ce dont on est
    sur qu'il est perime.
    """
    token = (creds or {}).get("AWS_SESSION_TOKEN", "")
    if not token or token.count(".") != 2:
        return True
    try:
        charge = token.split(".")[1]
        charge += "=" * (-len(charge) % 4)
        exp = json.loads(base64.urlsafe_b64decode(charge)).get("exp")
        if not exp:
            return True
        return float(exp) > time.time() + _STS_MARGE_S
    except Exception:
        return True


def _s3_creds_from_env(owner: str = "") -> dict[str, str] | None:
    """Creds S3 depuis l'env du pod (Onyxia injecte AWS_* + bucket lors du
    lancement du hub via le launcher datalab). Fallback quand le secret
    passerelle-s3-creds n'existe pas (cas onboarding standard d'un user).

    Bucket SSPCloud = nom d'utilisateur. Onyxia ne pose pas toujours un env
    dédié → on tente AWS_BUCKET_NAME / SSPCLOUD_BUCKET puis on dérive de
    ONYXIA_USER / WORKSPACE_OWNER.
    """
    akid = os.getenv("AWS_ACCESS_KEY_ID", "")
    if not akid:
        return None
    bucket = (os.getenv("AWS_BUCKET_NAME") or os.getenv("SSPCLOUD_BUCKET")
              or os.getenv("ONYXIA_USER") or os.getenv("WORKSPACE_OWNER")
              or owner or "")
    return {
        "AWS_ACCESS_KEY_ID":     akid,
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        "AWS_SESSION_TOKEN":     os.getenv("AWS_SESSION_TOKEN", ""),
        # Le chart injecte AWS_ENDPOINT_URL, Onyxia AWS_S3_ENDPOINT : on
        # accepte les deux. Sans cela, seule la valeur codee en dur restait,
        # juste par chance aujourd'hui et fausse des que l'endpoint change.
        "AWS_S3_ENDPOINT":       (os.getenv("AWS_S3_ENDPOINT")
                                  or os.getenv("AWS_ENDPOINT_URL")
                                  or "minio.lab.sspcloud.fr"),
        "AWS_DEFAULT_REGION":    os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        "SSPCLOUD_BUCKET":       bucket,
    }


def _read_passerelle_s3_creds(owner: str = "") -> dict[str, str]:
    now = time.time()
    if _creds_cache["data"] and (now - _creds_cache["ts"]) < _CREDS_TTL:
        return _creds_cache["data"]

    # 1) Secret K8s passerelle-s3-creds (déploiement avec creds long-lived).
    #
    # Correctif 2026-08-23 : le secret ne l'emporte plus aveuglement. Sur
    # l'instance de reference, un secret laisse par un ancien deploiement
    # portait un jeton expire depuis 27 jours, alors que le pod recevait des
    # identifiants valides du launcher Onyxia. Toute publication echouait sur
    # "Connection was closed before we received a valid response", sans que
    # rien ne designe la cause : le catalogue restait vide et le diagnostic
    # pointait le reseau. On verifie donc l'echeance avant de retenir le
    # secret, et on bascule sur l'environnement s'il est perime.
    secret_creds = None
    r = subprocess.run(
        ["kubectl", "get", "secret", _SECRET_NAME, "-o", "json"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0:
        try:
            data = json.loads(r.stdout)["data"]
            secret_creds = {
                k: base64.b64decode(v).decode() for k, v in data.items()
            }
        except Exception as exc:
            log.warning("secret %s illisible : %s", _SECRET_NAME, exc)

    if secret_creds and _sts_encore_valide(secret_creds):
        _creds_cache["data"] = secret_creds
        _creds_cache["ts"] = now
        return secret_creds

    # 2) Environnement du pod (onboarding Onyxia standard : pas de secret
    #    dédié, mais AWS_* injectés par le launcher datalab). Sert aussi de
    #    recours quand le secret existe mais porte un jeton perime.
    env_creds = _s3_creds_from_env(owner)
    if env_creds and env_creds.get("SSPCLOUD_BUCKET"):
        if secret_creds:
            log.warning(
                "secret %s ignore : jeton de session expire. Bascule sur les "
                "identifiants du pod. Supprimer ce secret s'il n'est plus "
                "utilise : kubectl delete secret %s",
                _SECRET_NAME, _SECRET_NAME,
            )
        _creds_cache["data"] = env_creds
        _creds_cache["ts"] = now
        return env_creds

    # 3) Dernier recours : le secret perime vaut mieux que rien, l'appel S3
    #    remontera une erreur explicite plutot qu'une absence de creds.
    if secret_creds:
        log.error(
            "secret %s expire ET environnement incomplet : la publication va "
            "echouer. Relance install.sh pour renouveler les acces.",
            _SECRET_NAME,
        )
        _creds_cache["data"] = secret_creds
        _creds_cache["ts"] = now
        return secret_creds

    raise RuntimeError(
        f"Creds S3 indisponibles : secret {_SECRET_NAME} absent "
        f"({r.stderr[:120]}) et AWS_* env incomplets (bucket manquant)."
    )


import boto3  # noqa: E402 — top-level pour que _S3_AVAILABLE détecte l'absence


def _get_s3_client(owner: str = ""):
    """Renvoie (client, bucket, endpoint).

    Sprint sec-vague0 dette OOM piste 1a v3 (2026-07-20) : ajoute une
    Config boto3 avec retries adaptifs + timeouts longs pour absorber
    les "Connection was closed before we received a valid response"
    observees sur les gros uploads (19-38MB) contre MinIO SSPCloud.
    - retries.mode=adaptive : backoff exponentiel + jitter (retry safer
      qu'un simple loop)
    - retries.max_attempts=5 : 4 retries apres le 1er echec
    - connect_timeout=10s : etablissement connexion TCP
    - read_timeout=300s : lecture data (5min - upload lent tolere)
    - tcp_keepalive=True : evite les coupures mid-upload par les
      middleboxes reseau
    """
    from botocore.config import Config as _BotoConfig
    creds = _read_passerelle_s3_creds(owner)
    endpoint = creds.get("AWS_S3_ENDPOINT", "https://minio.lab.sspcloud.fr").rstrip("/")
    if not endpoint.startswith("http"):
        endpoint = "https://" + endpoint
    bucket = creds["SSPCLOUD_BUCKET"]
    boto_cfg = _BotoConfig(
        retries={"max_attempts": 5, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=300,
        tcp_keepalive=True,
    )
    client = boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=creds["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=creds["AWS_SECRET_ACCESS_KEY"],
        # Une chaine vide n'est pas un jeton : elle ferait signer une requete
        # avec un en-tete de securite vide, que MinIO rejette. Des acces de
        # longue duree n'en portent pas -- il faut alors passer None.
        aws_session_token=creds.get("AWS_SESSION_TOKEN") or None,
        region_name=creds.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=boto_cfg,
    )
    return client, bucket, endpoint


# ── Diagnostic des erreurs S3 ─────────────────────────────────────────────────

# Codes renvoyes par MinIO/S3 quand les identifiants ne sont plus valables.
# Sur SSPCloud, les acces au stockage sont des jetons temporaires (STS) d'une
# duree de 7 jours : une semaine apres l'installation, toute publication
# echoue. Sans traduction, l'utilisateur ne voyait qu'une trace technique et
# concluait que "le service ne marche plus".
_S3_EXPIRED_CODES = (
    "ExpiredToken", "ExpiredTokenException", "TokenRefreshRequired",
    "InvalidToken", "InvalidAccessKeyId", "SignatureDoesNotMatch",
    "AccessDenied",
)

# Ce message a longtemps renvoye vers `install.sh`. C'etait une impasse :
# le script recopie les identifiants du service Jupyter, qui sont morts en
# meme temps que les autres (empreintes identiques verifiees le 2026-09-18).
# Il envoie desormais la ou le renouvellement marche vraiment.
_S3_EXPIRED_MESSAGE = (
    "Tes accès au stockage SSPCloud ont expiré (ils durent 7 jours). "
    "Le bandeau en haut du bureau propose « Renouveler » : ouvre "
    "datalab.sspcloud.fr > Mon compte > Connexion au stockage, copie le bloc "
    "affiché, et colle-le. Inutile de relancer l'installation, elle recopie "
    "les mêmes identifiants expirés."
)


def is_s3_credentials_expired(exc: Exception) -> bool:
    """Vrai si l'exception traduit des identifiants de stockage perimes."""
    code = ""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = (resp.get("Error") or {}).get("Code", "") or ""
    if isinstance(resp, dict):
        # Une requete HEAD n'a pas de corps : botocore ne peut pas y lire le
        # code d'erreur et se rabat sur le statut HTTP. Un 401/403 sur
        # HeadObject ne peut venir que d'identifiants refuses -- le stockage
        # s'arrete a l'authentification, il ne regarde jamais si l'objet
        # existe. Sans ce cas, le message utile ci-dessous n'etait jamais
        # atteint et l'utilisateur recevait le texte brut de la bibliotheque.
        statut = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if statut in (401, 403):
            return True
    haystack = f"{code} {exc}"
    return any(c in haystack for c in _S3_EXPIRED_CODES)


def explain_s3_error(exc: Exception) -> str:
    """Message destine a l'utilisateur pour une erreur de stockage.

    Renvoie une consigne actionnable quand les identifiants ont expire,
    et sinon la description technique d'origine.
    """
    if is_s3_credentials_expired(exc):
        return _S3_EXPIRED_MESSAGE
    return f"{type(exc).__name__}: {exc}"


# ── Helpers chemins ───────────────────────────────────────────────────────────

def _safe_slug(slug: str) -> str:
    """Slug DNS-like : alphanumérique + _ - uniquement."""
    out = "".join(c if c.isalnum() or c in "_-." else "_" for c in slug).strip("_-.")
    return out[:120]


def s3_key(owner: str, kind: str, slug: str) -> str:
    if kind not in _KINDS:
        raise ValueError(f"kind invalide: {kind} (attendu : {sorted(_KINDS)})")
    ext = _KIND_EXT[kind]
    s = _safe_slug(slug)
    return f"{_S3_PREFIX}/{owner}/{kind}/{s}.{ext}"


# ── Renouveler les acces au stockage, sans passer par install.sh ────────────
#
# Les identifiants S3 d'un service Onyxia sont temporaires : MinIO les delivre
# a la creation du service, pour sept jours, et personne ne les renouvelle.
# Passe ce delai, tout ce qui touche au stockage tombe -- catalogue vide,
# livrables invisibles, scenes inaccessibles -- sans que le code soit en cause.
#
# Mesure le 2026-09-18 : le service Jupyter de reference datait du 23 aout.
# Relancer `install.sh` n'y change RIEN : le script recopie fidelement les
# identifiants du pod jupyter, morts avec lui (empreintes identiques verifiees
# des deux cotes). Le message d'erreur du hub envoyait pourtant l'utilisateur
# exactement la -- dans une impasse.
#
# Le jeton d'identite d'un compte SSPCloud porte l'audience `minio-datanode` :
# il suffit donc a frapper STS directement, sans Onyxia et sans creer la
# moindre cle de service. C'est ce que fait cette fonction, et ce que le hub
# ne savait pas faire.

_STS_URL = os.getenv("MINIO_STS_URL", "https://minio.lab.sspcloud.fr")
_STS_DUREE_S = int(os.getenv("MINIO_STS_DUREE_S", "604800"))   # sept jours


def renouveler_les_acces(jeton_identite: str, bucket: str = "") -> dict[str, Any]:
    """Echange un jeton d'identite SSPCloud contre des acces au stockage.

    Rend `{"ok": True, "expire_le": ...}` et pose le resultat dans le secret,
    ou `{"ok": False, "erreur": ...}` en disant ce qui n'a pas marche. Ne leve
    pas : l'appelant est une interface, pas un script.
    """
    jeton = (jeton_identite or "").strip()
    if not jeton:
        return {"ok": False, "erreur": "Aucun jeton fourni."}
    if jeton.count(".") != 2:
        return {"ok": False, "erreur": (
            "Ceci ne ressemble pas a un jeton d'identite. Copie celui de "
            "datalab.sspcloud.fr > Mon compte."
        )}

    corps = urllib.parse.urlencode({
        "Action": "AssumeRoleWithWebIdentity",
        "Version": "2011-06-15",
        "WebIdentityToken": jeton,
        "DurationSeconds": str(_STS_DUREE_S),
    }).encode()
    try:
        requete = urllib.request.Request(
            _STS_URL, data=corps, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(requete, timeout=30) as reponse:
            xml = reponse.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        if exc.code in (400, 403):
            return {"ok": False, "erreur": (
                "Le stockage a refuse ce jeton. Il est peut-etre expire : "
                "reprends-en un sur datalab.sspcloud.fr > Mon compte."
            ), "detail": detail}
        return {"ok": False, "erreur": f"Le stockage a repondu {exc.code}.",
                "detail": detail}
    except Exception as exc:
        return {"ok": False,
                "erreur": f"Stockage injoignable ({type(exc).__name__})."}

    def _entre(balise: str) -> str:
        m = re.search(rf"<{balise}>(.*?)</{balise}>", xml, re.S)
        return m.group(1).strip() if m else ""

    creds = {
        "AWS_ACCESS_KEY_ID": _entre("AccessKeyId"),
        "AWS_SECRET_ACCESS_KEY": _entre("SecretAccessKey"),
        "AWS_SESSION_TOKEN": _entre("SessionToken"),
    }
    if not all(creds.values()):
        return {"ok": False,
                "erreur": "Reponse du stockage incomplete.", "detail": xml[:300]}

    creds["SSPCLOUD_BUCKET"] = bucket or _seau_courant()
    pose = _poser_le_secret(creds)
    if not pose.get("ok"):
        return pose

    _creds_cache["data"] = None          # la prochaine lecture repart du secret
    _creds_cache["ts"] = 0
    return {"ok": True, "expire_le": _entre("Expiration") or None,
            "bucket": creds["SSPCLOUD_BUCKET"]}


# ── Accepter ce qu'Onyxia affiche vraiment ──────────────────────────────────
#
# Mesure le 2026-09-20 : la page « Mon compte > Connexion au stockage » ne
# propose AUCUN jeton d'identite. Elle affiche des identifiants deja echanges,
# sous la forme de l'onglet choisi : export shell, fichier de configuration
# AWS, extrait Python boto3, ou une ligne `MC_HOST_...` ou la cle, le secret
# et le jeton sont colles dans une URL.
#
# Exiger un jeton d'identite revenait donc a demander ce que la page ne montre
# pas. On accepte desormais les deux : un jeton seul part vers STS, tout le
# reste est lu tel quel. L'utilisateur copie le bloc entier sans le trier --
# un copier-coller ne se trompe pas de champ, une saisie manuelle si.

_NOMS_D_IDENTIFIANT = {
    "AWS_ACCESS_KEY_ID": ("aws_access_key_id", "access_key_id", "accesskeyid",
                          "aws_access_key"),
    "AWS_SECRET_ACCESS_KEY": ("aws_secret_access_key", "secret_access_key",
                              "secretaccesskey", "aws_secret_key"),
    "AWS_SESSION_TOKEN": ("aws_session_token", "session_token", "sessiontoken",
                          "aws_security_token"),
    "SSPCLOUD_BUCKET": ("aws_bucket_name", "bucket_name", "sspcloud_bucket",
                        "aws_s3_bucket", "bucket"),
}

# Des noms de seau qui ne designent rien : la page les montre en exemple.
_SEAUX_FICTIFS = ("bucket", "your-bucket", "my-bucket", "mon-bucket",
                  "votre-bucket", "<bucket>", "nom-du-bucket")

# `MC_HOST_default='https://CLE:SECRET:JETON@minio.lab.sspcloud.fr'`
# La partie avant `@` ne peut pas contenir d'arobase : on la prend entiere,
# puis on la coupe en trois -- le jeton, qui vient en dernier, garde tout ce
# qui reste, y compris d'eventuels deux-points.
_MOTIF_MC_HOST = re.compile(
    r"MC_HOST_\w+\s*=\s*['\"]?https?://([^@\s'\"]+)@", re.I)


def extraire_des_identifiants(colle: str) -> dict[str, str]:
    """Reconnait des identifiants S3 dans ce que l'utilisateur a copie.

    Rend un dictionnaire des valeurs reconnues -- possiblement vide, jamais
    d'exception : l'appelant est une interface, pas un script.
    """
    if not colle or not colle.strip():
        return {}

    trouve: dict[str, str] = {}

    # La forme URL d'abord : elle porte les trois valeurs d'un coup, et ses
    # composants sont encodes, donc illisibles par la recherche par nom.
    m = _MOTIF_MC_HOST.search(colle)
    if m:
        morceaux = m.group(1).split(":", 2)
        for nom, valeur in zip(
            ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"),
            morceaux,
        ):
            decode = urllib.parse.unquote(valeur)
            if decode:
                trouve[nom] = decode

    # Puis la forme `NOM = valeur`, sous toutes ses ponctuations : avec ou
    # sans `export`, deux-points ou egal, guillemets simples ou doubles.
    for cible, noms in _NOMS_D_IDENTIFIANT.items():
        if trouve.get(cible):
            continue
        for nom in noms:
            motif = (rf"['\"]?{nom}['\"]?\s*[=:]\s*"
                     rf"['\"]?([A-Za-z0-9/+=._\-]{{3,}})['\"]?")
            trouvaille = re.search(motif, colle, re.I)
            if trouvaille:
                trouve[cible] = trouvaille.group(1)
                break

    if trouve.get("SSPCLOUD_BUCKET", "").lower() in _SEAUX_FICTIFS:
        trouve.pop("SSPCLOUD_BUCKET")
    return trouve


def _seau_courant() -> str:
    """Le nom du seau deja configure, quelle qu'en soit la provenance.

    Onyxia ne l'affiche pas toujours : l'onglet mc montre un seau d'exemple
    (`your-bucket`), les autres parfois rien. Sans ce repli, renouveler les
    acces effacait le nom qui marchait -- et toute publication tombait alors
    sur « Invalid bucket name "" », une panne creee par la reparation.
    Constate en production le 2026-09-20.
    """
    try:
        creds = _read_passerelle_s3_creds()
    except Exception:
        creds = {}
    return (creds.get("SSPCLOUD_BUCKET", "")
            or os.getenv("AWS_BUCKET_NAME", "")
            or os.getenv("SSPCLOUD_BUCKET", ""))


def _echeance_du_jeton(jeton: str) -> float | None:
    """L'instant ou ce jeton de session cesse d'etre valable, s'il le dit.

    Un identifiant de longue duree n'a pas de jeton, donc pas d'echeance :
    on rend `None`, et l'appelant en conclut qu'il n'y a rien a surveiller.
    """
    if not jeton or jeton.count(".") != 2:
        return None
    try:
        charge = jeton.split(".")[1]
        charge += "=" * (-len(charge) % 4)
        exp = json.loads(base64.urlsafe_b64decode(charge)).get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


def adopter_ce_qui_est_colle(colle: str, bucket: str = "") -> dict[str, Any]:
    """Enregistre des acces au stockage a partir d'un copier-coller.

    Choisit le chemin sur le contenu, pas sur un bouton : un jeton d'identite
    seul est echange aupres de STS ; tout le reste est lu comme des
    identifiants deja delivres.
    """
    texte = (colle or "").strip()
    if not texte:
        return {"ok": False, "erreur": "Rien n'a ete colle."}

    # Un jeton d'identite nu : trois segments, aucun espace, rien autour.
    if len(texte.split()) == 1 and texte.count(".") == 2:
        return renouveler_les_acces(texte, bucket)

    creds = extraire_des_identifiants(texte)
    manquants = [n for n in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
                 if not creds.get(n)]
    if manquants:
        return {"ok": False, "erreur": (
            "Je n'ai pas retrouve d'identifiants la-dedans. Sur "
            "datalab.sspcloud.fr > Mon compte > Connexion au stockage, copie "
            "un bloc entier (onglet shell, Python ou mc) et colle-le ici."
        )}

    seau = bucket or creds.get("SSPCLOUD_BUCKET", "") or _seau_courant()
    jeton = creds.get("AWS_SESSION_TOKEN", "")
    a_poser: dict[str, str | None] = {
        "AWS_ACCESS_KEY_ID": creds["AWS_ACCESS_KEY_ID"],
        "AWS_SECRET_ACCESS_KEY": creds["AWS_SECRET_ACCESS_KEY"],
        # Pas de jeton = acces de longue duree : il faut RETIRER l'ancien,
        # sinon le service signerait avec un jeton mort.
        "AWS_SESSION_TOKEN": jeton or None,
        "SSPCLOUD_BUCKET": seau,
    }
    pose = _poser_le_secret(a_poser)
    if not pose.get("ok"):
        return pose

    _creds_cache["data"] = None          # la prochaine lecture repart du secret
    _creds_cache["ts"] = 0

    fin = _echeance_du_jeton(jeton)
    return {
        "ok": True,
        "bucket": seau,
        "permanents": fin is None,
        "expire_le": (time.strftime("%Y-%m-%d %H:%M", time.localtime(fin))
                      if fin else None),
    }


def _poser_le_secret(creds: dict[str, str | None]) -> dict[str, Any]:
    """Ecrit les identifiants dans le secret, en preservant ce qu'il portait.

    Un `create` ecraserait les autres cles du secret (HF_TOKEN,
    S3_ENCRYPT_KEY...). On fusionne donc, et on cree s'il n'existe pas.

    Trois cas, volontairement distincts -- les confondre casse a coup sur :

      * une valeur -> elle est ecrite ;
      * une chaine vide = « je ne sais pas » -> la cle est laissee telle
        quelle. L'ecrire effacerait ce qui marchait, et la reparation
        creerait la panne (« Invalid bucket name "" », vu en production le
        2026-09-20) ;
      * `None` = « il n'y en a pas » -> la cle est RETIREE. Des acces de
        longue duree n'ont pas de jeton de session : laisser l'ancien en
        place ferait signer avec un jeton mort.
    """
    donnees: dict[str, str | None] = {}
    for cle, valeur in creds.items():
        if valeur:
            donnees[cle] = base64.b64encode(valeur.encode()).decode()
        elif valeur is None:
            donnees[cle] = None          # un null en merge patch supprime
    if not any(v for v in donnees.values()):
        return {"ok": False, "erreur": "Aucun identifiant a enregistrer."}
    patch = json.dumps({"data": donnees})
    r = subprocess.run(
        ["kubectl", "patch", "secret", _SECRET_NAME, "--type=merge", "-p", patch],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode == 0:
        return {"ok": True}

    # Le secret n'existe pas encore : on le cree. Il n'y a donc rien a
    # supprimer, et un `null` y serait refuse par l'API.
    manifeste = json.dumps({
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": _SECRET_NAME},
        "data": {k: v for k, v in donnees.items() if v is not None},
    })
    r2 = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifeste, capture_output=True, text=True, timeout=20,
    )
    if r2.returncode == 0:
        return {"ok": True}
    return {"ok": False, "erreur": (
        "Les acces ont ete obtenus, mais le service n'a pas pu les "
        "enregistrer. Il lui manque le droit d'ecrire ses propres secrets."
    ), "detail": (r2.stderr or r.stderr)[:300]}


def etat_des_acces() -> dict[str, Any]:
    """Les acces au stockage tiennent-ils encore, et jusqu'a quand.

    Rien ne le disait a l'utilisateur : il decouvrait la panne en constatant
    que ses livrables avaient disparu. Depuis le depot local, ils ne
    disparaissent plus -- l'etat rendu porte donc aussi `livrables_servis`,
    pour que l'interface annonce ce qui est vrai et rien de plus.
    """
    try:
        depot = depot_local.etat()
    except Exception:
        depot = {"disponible": False}

    try:
        creds = _read_passerelle_s3_creds()
    except Exception as exc:
        return {"valides": False, "raison": str(exc)[:200],
                "depot_local": depot,
                "livrables_servis": bool(depot.get("disponible"))}

    exp = _echeance_du_jeton(creds.get("AWS_SESSION_TOKEN", ""))
    if exp is None:
        # Pas d'echeance lisible : identifiants de longue duree, rien a veiller.
        return {"valides": True, "permanents": True, "depot_local": depot,
                "livrables_servis": True}

    restant = exp - time.time()
    return {
        "valides": restant > _STS_MARGE_S,
        "permanents": False,
        "expire_dans_heures": round(restant / 3600, 1),
        "expire_le": time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(float(exp))),
        "depot_local": depot,
        # Les livrables restent servis par le hub meme sans acces au
        # stockage. L'interface doit le dire : annoncer une indisponibilite
        # qui n'existe plus inquieterait pour rien.
        "livrables_servis": bool(depot.get("disponible")),
    }


def public_url(endpoint: str, bucket: str, key: str) -> str:
    return f"{endpoint.rstrip('/')}/{bucket}/{key}"


# ── Le depot local, et pourquoi il passe devant ─────────────────────────────
#
# Une publication ne vivait que sur MinIO. Les identifiants d'un service
# Onyxia durent sept jours et personne ne les renouvelle : passe ce delai, le
# catalogue se vidait, les livrables disparaissaient et les scenes
# repondaient 503. Rien n'etait perdu, mais tout semblait l'etre.
#
# Le hub ecrit donc d'abord sur son propre disque, qui n'expire pas, et S3
# n'est plus qu'un second exemplaire -- utile pour partager en dehors du
# service, plus indispensable pour lire. Une expiration d'acces cesse d'etre
# une panne : elle devient une simple impossibilite de mettre a jour la copie
# distante.

from hub import depot_local  # noqa: E402 — apres public_url, avant l'API

# L'adresse du hub, pour rendre un lien qui marche quand S3 n'a pas repondu.
# Derivee comme dans `main.py`, a partir de ce que SSPCloud injecte.
_HUB_URL = (
    os.getenv("HUB_URL")
    or (f"https://user-{os.getenv('ONYXIA_USER')}-qgis.user.lab.sspcloud.fr"
        if os.getenv("ONYXIA_USER") else "")
)


def _lien_hub(owner: str, kind: str, slug: str) -> str:
    """L'adresse par laquelle le hub sert lui-meme cette publication."""
    base = _HUB_URL.rstrip("/")
    return f"{base}/published/{owner}/{kind}/{_safe_slug(slug)}"


# ── API publique ──────────────────────────────────────────────────────────────

def publish(owner: str, kind: str, slug: str, content: bytes,
            content_type: str | None = None,
            study_id: str | None = None,
            audience: str = "cerema_internal") -> dict:
    """
    Publie un livrable. Retourne {url, key, kind, slug, size, study_id, audience}.

    Sprint sec-rgpd P0-1+P0-2 (2026-07-19) : audience obligatoire propagé
    dans metadata S3 pour permettre `serve_published` d'appliquer un gate
    sur reads (P0-1). ACL S3 conditionnel selon audience (P0-2) :
        - "public"           -> ACL public-read (URL MinIO directe accessible)
        - "cerema_internal"  -> ACL private (lecture uniquement via hub /published)
        - "restricted"       -> ACL private
        - "confidential"     -> ACL private
    Default "cerema_internal" (anti-fuite : safe by default).

    Publications historiques (sans metadata audience) : au next re-publish,
    l'ACL est mise a jour selon l'audience courante. Les objets existants
    en public-read restent accessibles via MinIO direct jusqu'a leur
    prochain publish - un script batch re-ACL est prevu en suite.

    Phase 13 : `study_id` lie la publication à l'étude qui l'a produite.
    Sert pour la traçabilité (provenance des données) et l'UI desk
    (grouper les publications par étude).
    """
    key = s3_key(owner, kind, slug)
    ct = content_type or _KIND_CONTENT_TYPE.get(kind, "application/octet-stream")

    # Le depot local en premier, avant toute compression : il conserve
    # l'objet tel quel, et il n'a besoin d'aucun identifiant. Si S3 est hors
    # d'atteinte, c'est lui qui rendra la publication lisible.
    depose = depot_local.ecrire(
        owner, kind, slug, _KIND_EXT.get(kind, "bin"), content,
    )

    # Validation audience (anti-injection metadata). Valeurs conformes au
    # Literal Classification (hub/hub/models/classification.py).
    valid_audiences = {"public", "cerema_internal", "restricted", "confidential"}
    if audience not in valid_audiences:
        log.warning("publish: audience %r invalide, fallback cerema_internal", audience)
        audience = "cerema_internal"

    metadata = {
        "owner":    owner,
        "kind":     kind,
        "slug":     _safe_slug(slug),
        "published-at": str(int(time.time())),
        "audience": audience,
    }
    if study_id:
        metadata["study-id"] = study_id

    # ACL conditionnel : seul audience=public reste directement accessible
    # via MinIO. Les autres passent obligatoirement par le hub qui applique
    # le gate serve_published.
    s3_acl = "public-read" if audience == "public" else "private"

    # Sprint sec-vague0 dette OOM piste 1a v4 (2026-07-21) : historique
    # des tentatives sur les gros uploads (19-38MB) contre MinIO SSPCloud :
    # - v1 : put_object direct -> "Connection was closed before valid
    #        response" (TCP timeout ~15-30s sur upload lent 19MB).
    # - v2 : upload_fileobj + TransferConfig multipart 5MB -> erreur
    #        "InvalidAccessKeyId sur CreateMultipartUpload". Le token
    #        stsonly SSPCloud n'a PAS la permission
    #        s3:CreateMultipartUpload (restriction policy).
    # - v3 : put_object + boto3 Config retries adaptive + timeouts 300s.
    #        Retry adaptive INSUFFISANT : MinIO ferme la connexion apres
    #        ~15-20s systematiquement sur payloads 19MB+, chaque retry
    #        echoue de la meme facon (hard limit infra SSPCloud).
    # - v4 (actuel) : gzip Content-Encoding cote client + put_object du
    #        payload compresse. Un GeoJSON gzip ~= -70% (19MB -> ~6MB).
    #        Passe sous le seuil connection close. MinIO sert avec
    #        Content-Encoding: gzip, MapLibre / navigateur decompressent
    #        nativement via HTTP. Compatible aussi pour les HTML publish
    #        (38MB -> ~10-12MB). Threshold gzip = 2MB pour eviter overhead
    #        sur petits objets.
    import gzip as _gzip
    _GZIP_THRESHOLD = 2 * 1024 * 1024  # 2MB
    # Piste PMTiles V0.4 (2026-07-21) : les .pmtiles sont deja compresses
    # en interne (zstd/gzip sur les tuiles MVT). Recompresser cote HTTP
    # ajoute du CPU pour un gain nul + brise le magic byte reader
    # (pmtiles.reader attend le magic "PMTiles" en byte 0 non-encapsule).
    # Skip gzip pour ce kind.
    if kind != "features_pmtiles" and len(content) > _GZIP_THRESHOLD:
        content = _gzip.compress(content, compresslevel=6)
        extra_kwargs = {"ContentEncoding": "gzip"}
    else:
        extra_kwargs = {}

    # S3 n'est plus qu'un second exemplaire. Son echec ne doit plus faire
    # echouer la publication tant que le depot local l'a acceptee : le
    # livrable existe, il est lisible, il manque seulement sa copie distante.
    # Avant, une expiration d'identifiants rendait la publication impossible
    # -- et le travail semblait perdu.
    sur_s3, url, echec_s3 = False, "", ""
    try:
        client, bucket, endpoint = _get_s3_client(owner)
        client.put_object(
            Bucket=bucket, Key=key, Body=content,
            ContentType=ct,
            ACL=s3_acl,
            Metadata=metadata,
            **extra_kwargs,
        )
        sur_s3 = True
        url = public_url(endpoint, bucket, key)
    except Exception as exc:
        # Les acces au stockage SSPCloud sont des jetons temporaires (7 jours).
        # Passe ce delai, l'ecriture echoue et l'utilisateur ne voyait qu'une
        # trace botocore : il en concluait que le service etait casse. On
        # remonte une consigne actionnable a la place.
        echec_s3 = (_S3_EXPIRED_MESSAGE if is_s3_credentials_expired(exc)
                    else f"{type(exc).__name__}: {exc}"[:300])
        if not depose:
            log.error("publish %s/%s : ni depot local ni S3 (%s)",
                      owner, slug, type(exc).__name__)
            if is_s3_credentials_expired(exc):
                raise RuntimeError(_S3_EXPIRED_MESSAGE) from exc
            raise
        log.warning("publish %s/%s : garde en local, S3 refuse (%s)",
                    owner, slug, type(exc).__name__)

    info = {
        "url":      url or _lien_hub(owner, kind, slug),
        "key":      key,
        "kind":     kind,
        "slug":     _safe_slug(slug),
        "owner":    owner,
        "size":     len(content),
        "published_at": int(time.time()),
        "content_type": ct,
        "audience": audience,
        "acl":      s3_acl,
        # Ou vit reellement cette publication. L'interface peut ainsi dire
        # « en ligne, pas encore recopiee sur le stockage » plutot que de
        # laisser croire a un succes complet.
        "local":    depose,
        "distant":  sur_s3,
    }
    if echec_s3:
        info["stockage_distant_refuse"] = echec_s3
    if study_id:
        info["study_id"] = study_id
    # MAJ catalogue user
    _update_catalog(owner, info)
    return info


def read(owner: str, kind: str, slug: str) -> bytes | None:
    """Récupère le contenu d'une publication. None si absent.

    Sprint sec-vague0 dette OOM piste 1a v4 (2026-07-21) : les publish
    > 2MB sont stockes gzip-compresses avec ContentEncoding=gzip (pour
    contourner le "Connection was closed" sur uploads MinIO SSPCloud
    des gros objets 19-38MB). On decompresse ici pour que serve_published
    retourne bytes uncompressed comme avant, transparent pour le client.
    """
    key = s3_key(owner, kind, slug)
    # Le disque du hub d'abord : il est plus rapide, il ne peut pas expirer,
    # et il conserve l'objet non compresse.
    local = depot_local.lire(owner, kind, slug, _KIND_EXT.get(kind, "bin"))
    if local is not None:
        return local

    client, bucket, _ = _get_s3_client(owner)
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        # Auto-decompresse si le publish etait gzip
        if obj.get("ContentEncoding") == "gzip":
            import gzip as _gzip
            body = _gzip.decompress(body)
        # Publication d'avant le depot local : on en prend une copie au
        # passage, pour qu'elle survive a la prochaine expiration.
        depot_local.ecrire(owner, kind, slug, _KIND_EXT.get(kind, "bin"), body)
        return body
    except client.exceptions.NoSuchKey:
        return None
    except Exception as exc:
        log.warning("S3 read failed %s/%s: %s", owner, slug, exc)
        return None


def read_range(
    owner: str, kind: str, slug: str, byte_range: str,
) -> dict | None:
    """Lit un range partiel d'une publication S3. Utilise pour PMTiles.

    Sprint sec-vague0 dette OOM piste PMTiles V0.4 Commit 5 (2026-07-21) :
    le protocol pmtiles-protocol MapLibre fetch les tuiles via HTTP Range
    Requests (chunks 16KB). Ce helper forward le Range header client vers
    S3 GetObject(Range=...) et retourne les bytes partiels + les metadonnees
    necessaires pour construire une reponse HTTP 206 Partial Content.

    Note : les .pmtiles ne sont JAMAIS gzip-compresses cote S3 (kind
    "features_pmtiles" skip Content-Encoding dans publish()). Le magic
    byte "PMTiles" doit rester lisible en byte 0 pour que pmtiles.reader
    puisse parser le header, et le Range doit adresser les bytes bruts.

    Args:
        owner, kind, slug : identifiants publication (kind attendu :
                            "features_pmtiles").
        byte_range : header Range client (ex. "bytes=0-16383" ou
                     "bytes=1000-").

    Returns:
        None si publication absente. Sinon dict :
            {
              "body": bytes,             # bytes partiels
              "content_range": str,      # ex. "bytes 0-16383/2048576"
              "content_length": int,     # taille des bytes partiels
              "content_type": str,       # ex. "application/vnd.pmtiles"
              "total_size": int,         # taille totale du fichier
            }
    """
    key = s3_key(owner, kind, slug)
    local = depot_local.lire_intervalle(
        owner, kind, slug, _KIND_EXT.get(kind, "bin"), byte_range,
        _KIND_CONTENT_TYPE.get(kind, "application/octet-stream"),
    )
    if local is not None:
        return local

    client, bucket, _ = _get_s3_client(owner)
    try:
        obj = client.get_object(Bucket=bucket, Key=key, Range=byte_range)
        body = obj["Body"].read()
        # boto3 renvoie ContentRange sous forme "bytes 0-16383/2048576"
        content_range = obj.get("ContentRange", "")
        total_size = 0
        if content_range and "/" in content_range:
            try:
                total_size = int(content_range.split("/")[-1])
            except Exception:
                pass
        return {
            "body": body,
            "content_range": content_range,
            "content_length": len(body),
            "content_type": obj.get("ContentType", "application/octet-stream"),
            "total_size": total_size,
        }
    except client.exceptions.NoSuchKey:
        return None
    except Exception as exc:
        log.warning("S3 read_range failed %s/%s %s: %s", owner, slug, byte_range, exc)
        return None


class StockageInaccessible(RuntimeError):
    """Le stockage n'a pas pu etre interroge — l'objet peut exister.

    Distincte d'une absence : elle porte un message destine a l'utilisateur,
    et l'appelant doit repondre autre chose qu'un « introuvable ».
    """


def _est_absence_reelle(exc: Exception) -> bool:
    """Vrai si le stockage a repondu « cet objet n'existe pas ».

    Tout le reste — identifiants expires, refus, panne reseau — est une
    impossibilite de lire, pas une absence.
    """
    code = ""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = str((resp.get("Error") or {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NoSuchBucket", "NotFound")


def _metadata_insensible_casse(brut: dict) -> dict:
    """Les métadonnées S3, lisibles quelle que soit la casse des clés.

    MinIO les traite comme des en-têtes HTTP et les rend capitalisées :
    `audience` écrit revient en `Audience`. Un appelant qui lit la clé qu'il a
    posée ne la retrouve donc pas -- et si cette clé commande un contrôle
    d'accès, l'absence est interprétée comme un refus.

    On conserve les clés d'origine et on ajoute leur version minuscule : rien
    ne casse pour qui inspecte le dictionnaire, et `.get("audience")`
    fonctionne enfin.
    """
    if not isinstance(brut, dict):
        return {}
    sortie = dict(brut)
    for cle, valeur in brut.items():
        minuscule = cle.lower()
        if minuscule not in sortie:
            sortie[minuscule] = valeur
    return sortie


def _entree_du_catalogue(owner: str, kind: str, slug: str) -> dict:
    """Ce que l'index local sait de cette publication. Vide s'il l'ignore."""
    try:
        items = depot_local.catalogue(owner) or []
    except Exception:
        return {}
    voulu = _safe_slug(slug)
    for item in items:
        if item.get("kind") == kind and item.get("slug") == voulu:
            return item
    return {}


def head(owner: str, kind: str, slug: str) -> dict | None:
    """Métadonnées d'une publication sans télécharger le body.

    Le depot local repond en premier. L'audience vient alors de l'index
    local ; a defaut, du defaut restrictif -- une publication dont on ignore
    l'audience ne doit pas devenir publique par accident.
    """
    key = s3_key(owner, kind, slug)
    ext = _KIND_EXT.get(kind, "bin")
    sur_disque = depot_local.entete(owner, kind, slug, ext)
    if sur_disque is not None:
        entree = _entree_du_catalogue(owner, kind, slug)
        metadonnees = {
            "owner": owner,
            "kind": kind,
            "slug": _safe_slug(slug),
            "audience": entree.get("audience", "cerema_internal"),
        }
        if entree.get("study_id"):
            metadonnees["study-id"] = entree["study_id"]
        return {
            "key":           key,
            "url":           entree.get("url") or _lien_hub(owner, kind, slug),
            "size":          sur_disque["size"],
            "content_type":  entree.get("content_type") or _KIND_CONTENT_TYPE.get(
                kind, "application/octet-stream"),
            "last_modified": sur_disque["last_modified"],
            "metadata":      _metadata_insensible_casse(metadonnees),
            "depuis":        "local",
        }

    client, bucket, endpoint = _get_s3_client(owner)
    try:
        h = client.head_object(Bucket=bucket, Key=key)
        return {
            "key":          key,
            "url":          public_url(endpoint, bucket, key),
            "size":         h["ContentLength"],
            "content_type": h.get("ContentType", ""),
            "last_modified": int(h["LastModified"].timestamp()),
            # Les clés de métadonnées reviennent capitalisées : nous écrivons
            # `audience`, S3 rend `Audience`. Il les traite comme des en-têtes
            # HTTP, où la casse ne signifie rien. Les appelants, eux, lisaient
            # `audience` en minuscules et ne trouvaient rien -- donc toute
            # publication déclarée publique retombait sur le défaut restrictif
            # et répondait 401. On rend les deux graphies : celle d'origine,
            # pour qui inspecte, et la minuscule, pour qui interroge.
            "metadata":     _metadata_insensible_casse(h.get("Metadata", {})),
        }
    except Exception as exc:
        # « Introuvable » et « illisible » ne sont pas la meme chose. Rendre
        # None pour les deux faisait repondre 404 a un lecteur dont l'objet
        # existait parfaitement -- il en concluait qu'il avait mal publie.
        if _est_absence_reelle(exc):
            return None
        raise StockageInaccessible(explain_s3_error(exc)) from exc


def delete(owner: str, kind: str, slug: str) -> bool:
    """Dépublie. True si suppression effective.

    Les deux exemplaires partent ensemble. Oublier le local ferait
    reapparaitre un livrable que l'utilisateur croyait retire -- pire qu'une
    suppression qui echoue, parce que silencieux.
    """
    key = s3_key(owner, kind, slug)
    retire_localement = depot_local.supprimer(
        owner, kind, slug, _KIND_EXT.get(kind, "bin"))
    try:
        client, bucket, _ = _get_s3_client(owner)
        client.delete_object(Bucket=bucket, Key=key)
        _remove_from_catalog(owner, kind, slug)
        return True
    except Exception as exc:
        log.warning("S3 delete failed %s/%s: %s", owner, slug, exc)
        if retire_localement:
            # Le livrable n'est plus servi : la depublication a bien eu lieu
            # de notre cote. On retire aussi l'entree de l'index.
            try:
                _remove_from_catalog(owner, kind, slug)
            except Exception:
                pass
            return True
        return False


def list_published(owner: str, kind: str | None = None) -> list[dict]:
    """Liste les publications d'un owner. Filtrable par kind."""
    client, bucket, endpoint = _get_s3_client(owner)
    prefix = f"{_S3_PREFIX}/{owner}/"
    if kind:
        if kind not in _KINDS:
            raise ValueError(f"kind invalide: {kind}")
        prefix = f"{_S3_PREFIX}/{owner}/{kind}/"

    items = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # key = qgis-workspace/published/{owner}/{kind}/{slug}.{ext}
            parts = key.split("/")
            if len(parts) < 5:
                continue
            k = parts[3]
            slug_with_ext = parts[4]
            slug = slug_with_ext.rsplit(".", 1)[0]
            items.append({
                "owner":   owner,
                "kind":    k,
                "slug":    slug,
                "url":     public_url(endpoint, bucket, key),
                "key":     key,
                "size":    obj["Size"],
                "last_modified": int(obj["LastModified"].timestamp()),
            })
    items.sort(key=lambda x: x["last_modified"], reverse=True)
    return items


# ── Catalogue per-user ────────────────────────────────────────────────────────

def _catalog_key(owner: str) -> str:
    return f"{_CATALOG_PREFIX}/{owner}.json"


def get_catalog(owner: str) -> list[dict]:
    """Retourne l'index complet des publications.

    Le depot local passe devant : c'est lui qui repond quand les acces au
    stockage ont expire. Sans cela, le catalogue devenait illisible et
    l'utilisateur voyait « aucun livrable » alors qu'il en avait -- le
    symptome le plus courant de l'expiration, et le plus trompeur.
    """
    local = depot_local.catalogue(owner)
    if local is not None:
        return local

    client, bucket, _ = _get_s3_client(owner)
    try:
        obj = client.get_object(Bucket=bucket, Key=_catalog_key(owner))
        items = json.loads(obj["Body"].read())
    except client.exceptions.NoSuchKey:
        # Vraie absence : personne n'a encore publie. Le vide est la reponse.
        return []
    except Exception as exc:
        if _est_absence_reelle(exc):
            return []
        # Illisible : rendre [] ferait annoncer « aucune publication » a
        # quelqu'un dont les publications existent toujours.
        raise StockageInaccessible(explain_s3_error(exc)) from exc

    # Premiere lecture depuis S3 : on en garde une copie, pour que la
    # prochaine expiration ne vide plus rien.
    if isinstance(items, list):
        depot_local.ecrire_catalogue(owner, items)
        return items
    return []


def _save_catalog(owner: str, items: list[dict]) -> None:
    """Enregistre l'index, localement d'abord puis sur S3 si possible.

    Ne leve pas quand S3 refuse : l'index local suffit a retrouver ses
    livrables, et perdre la publication pour un index non recopie serait
    disproportionne.
    """
    ecrit_localement = depot_local.ecrire_catalogue(owner, items)
    body = json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        client, bucket, _ = _get_s3_client(owner)
        client.put_object(
            Bucket=bucket,
            Key=_catalog_key(owner),
            Body=body,
            ContentType="application/json; charset=utf-8",
            ACL="public-read",
        )
    except Exception as exc:
        if not ecrit_localement:
            raise
        log.warning("catalogue de %s : garde en local, S3 refuse (%s)",
                    owner, type(exc).__name__)


def _update_catalog(owner: str, info: dict) -> None:
    """Ajoute ou met à jour une entrée. Idempotent par (kind, slug).

    Un index illisible ne doit pas faire perdre la publication : a la
    premiere publication d'un compte, le catalogue local n'existe pas encore
    et S3 peut deja etre inaccessible. On repart alors d'un index vide
    plutot que d'echouer -- `rebuild_catalog` sait le reconstruire.
    """
    try:
        items = get_catalog(owner)
    except StockageInaccessible:
        log.warning("catalogue de %s illisible : on repart d'un index vide",
                    owner)
        items = []
    items = [i for i in items if not (i["kind"] == info["kind"] and i["slug"] == info["slug"])]
    items.append(info)
    items.sort(key=lambda x: x.get("published_at", 0), reverse=True)
    _save_catalog(owner, items)


def _remove_from_catalog(owner: str, kind: str, slug: str) -> None:
    items = get_catalog(owner)
    slug_safe = _safe_slug(slug)
    items = [i for i in items if not (i["kind"] == kind and i["slug"] == slug_safe)]
    _save_catalog(owner, items)


def rebuild_catalog(owner: str) -> list[dict]:
    """Reconstruit le catalogue à partir d'une liste S3 (consistance)."""
    items = list_published(owner)
    # Enrichir avec content_type via HEAD si possible
    for it in items:
        h = head(owner, it["kind"], it["slug"])
        if h:
            it["content_type"] = h.get("content_type", "")
            it["published_at"] = it.get("last_modified", 0)
    _save_catalog(owner, items)
    return items


def purge_all_publications(owner: str) -> dict:
    """Supprime TOUTES les publications d'un owner (S3 + catalogue).

    Cas d'usage : cleanup apres tests de dev / artefacts residuels.
    Renvoie {"deleted": N, "errors": [...]}. Operation DESTRUCTIVE et
    IRREVERSIBLE — les liens publics vers ces publications cassent.
    """
    items = list_published(owner)
    deleted = 0
    errors: list[str] = []
    client, bucket, _ = _get_s3_client(owner)
    for it in items:
        try:
            client.delete_object(Bucket=bucket, Key=it["key"])
            deleted += 1
        except Exception as exc:
            errors.append(f"{it['key']}: {type(exc).__name__}: {exc}")
            log.warning("purge_all_publications: %s/%s echec : %s",
                        owner, it["key"], exc)
    # Ecrase le catalogue avec liste vide (idempotent meme si delete partiel).
    try:
        _save_catalog(owner, [])
    except Exception as exc:
        errors.append(f"catalog: {type(exc).__name__}: {exc}")
        log.warning("purge_all_publications: save_catalog vide echec : %s", exc)
    return {"owner": owner, "deleted": deleted,
            "total_listed": len(items), "errors": errors}
