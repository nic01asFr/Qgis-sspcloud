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
import subprocess
import time
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
        aws_session_token=creds.get("AWS_SESSION_TOKEN"),
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

_S3_EXPIRED_MESSAGE = (
    "Tes accès au stockage SSPCloud ont expiré (ils sont valables 7 jours). "
    "Relance l'installation depuis un terminal de ton service Jupyter pour les "
    "renouveler : "
    "curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash"
)


def is_s3_credentials_expired(exc: Exception) -> bool:
    """Vrai si l'exception traduit des identifiants de stockage perimes."""
    code = ""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = (resp.get("Error") or {}).get("Code", "") or ""
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


def public_url(endpoint: str, bucket: str, key: str) -> str:
    return f"{endpoint.rstrip('/')}/{bucket}/{key}"


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
    client, bucket, endpoint = _get_s3_client(owner)
    key = s3_key(owner, kind, slug)
    ct = content_type or _KIND_CONTENT_TYPE.get(kind, "application/octet-stream")

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

    try:
        client.put_object(
            Bucket=bucket, Key=key, Body=content,
            ContentType=ct,
            ACL=s3_acl,
            Metadata=metadata,
            **extra_kwargs,
        )
    except Exception as exc:
        # Les acces au stockage SSPCloud sont des jetons temporaires (7 jours).
        # Passe ce delai, l'ecriture echoue et l'utilisateur ne voyait qu'une
        # trace botocore : il en concluait que le service etait casse. On
        # remonte une consigne actionnable a la place.
        if is_s3_credentials_expired(exc):
            log.error(
                "publish %s/%s : identifiants de stockage expires (%s)",
                owner, slug, type(exc).__name__,
            )
            raise RuntimeError(_S3_EXPIRED_MESSAGE) from exc
        raise
    url = public_url(endpoint, bucket, key)
    info = {
        "url":      url,
        "key":      key,
        "kind":     kind,
        "slug":     _safe_slug(slug),
        "owner":    owner,
        "size":     len(content),
        "published_at": int(time.time()),
        "content_type": ct,
        "audience": audience,
        "acl":      s3_acl,
    }
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
    client, bucket, _ = _get_s3_client(owner)
    key = s3_key(owner, kind, slug)
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        # Auto-decompresse si le publish etait gzip
        if obj.get("ContentEncoding") == "gzip":
            import gzip as _gzip
            body = _gzip.decompress(body)
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
    client, bucket, _ = _get_s3_client(owner)
    key = s3_key(owner, kind, slug)
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


def head(owner: str, kind: str, slug: str) -> dict | None:
    """Métadonnées d'une publication sans télécharger le body."""
    client, bucket, endpoint = _get_s3_client(owner)
    key = s3_key(owner, kind, slug)
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
    except Exception:
        return None


def delete(owner: str, kind: str, slug: str) -> bool:
    """Dépublie. True si suppression effective."""
    client, bucket, _ = _get_s3_client(owner)
    key = s3_key(owner, kind, slug)
    try:
        client.delete_object(Bucket=bucket, Key=key)
        _remove_from_catalog(owner, kind, slug)
        return True
    except Exception as exc:
        log.warning("S3 delete failed %s/%s: %s", owner, slug, exc)
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
    """Retourne l'index complet (depuis le JSON catalogue S3)."""
    client, bucket, _ = _get_s3_client(owner)
    try:
        obj = client.get_object(Bucket=bucket, Key=_catalog_key(owner))
        return json.loads(obj["Body"].read())
    except client.exceptions.NoSuchKey:
        return []
    except Exception:
        return []


def _save_catalog(owner: str, items: list[dict]) -> None:
    client, bucket, _ = _get_s3_client(owner)
    body = json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")
    client.put_object(
        Bucket=bucket,
        Key=_catalog_key(owner),
        Body=body,
        ContentType="application/json; charset=utf-8",
        ACL="public-read",
    )


def _update_catalog(owner: str, info: dict) -> None:
    """Ajoute ou met à jour une entrée. Idempotent par (kind, slug)."""
    items = get_catalog(owner)
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
