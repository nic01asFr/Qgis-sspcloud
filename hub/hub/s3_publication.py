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
}
_KIND_CONTENT_TYPE = {
    "storymap":  "text/html; charset=utf-8",
    "flux":      "application/octet-stream",
    "recipe":    "application/x-yaml; charset=utf-8",
    "dataset":   "application/geopackage+sqlite3",
    "pdf":       "application/pdf",
    "assembly":  "text/html; charset=utf-8",
    "features":  "application/geo+json; charset=utf-8",
    "component": "text/html; charset=utf-8",
}

# Chemin du secret passerelle (creds long-lived service-side)
_SECRET_NAME = os.getenv("PASSERELLE_S3_SECRET", "passerelle-s3-creds")
_S3_PREFIX = "qgis-workspace/published"
_CATALOG_PREFIX = "qgis-workspace/catalog"


# ── Lecture credentials passerelle (cached, refresh hourly) ──────────────────

_creds_cache: dict[str, Any] = {"ts": 0, "data": None}
_CREDS_TTL = 3600  # re-lit le secret toutes les heures (token STS rotation)


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
        "AWS_S3_ENDPOINT":       os.getenv("AWS_S3_ENDPOINT", "minio.lab.sspcloud.fr"),
        "AWS_DEFAULT_REGION":    os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        "SSPCLOUD_BUCKET":       bucket,
    }


def _read_passerelle_s3_creds(owner: str = "") -> dict[str, str]:
    now = time.time()
    if _creds_cache["data"] and (now - _creds_cache["ts"]) < _CREDS_TTL:
        return _creds_cache["data"]

    # 1) Secret K8s passerelle-s3-creds (déploiement avec creds long-lived).
    r = subprocess.run(
        ["kubectl", "get", "secret", _SECRET_NAME, "-o", "json"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0:
        data = json.loads(r.stdout)["data"]
        decoded = {k: base64.b64decode(v).decode() for k, v in data.items()}
        _creds_cache["data"] = decoded
        _creds_cache["ts"] = now
        return decoded

    # 2) Fallback env (onboarding Onyxia standard : pas de secret dédié, mais
    #    AWS_* injectés dans le pod hub par le launcher datalab).
    env_creds = _s3_creds_from_env(owner)
    if env_creds and env_creds.get("SSPCLOUD_BUCKET"):
        _creds_cache["data"] = env_creds
        _creds_cache["ts"] = now
        return env_creds

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
    if len(content) > _GZIP_THRESHOLD:
        content = _gzip.compress(content, compresslevel=6)
        extra_kwargs = {"ContentEncoding": "gzip"}
    else:
        extra_kwargs = {}

    client.put_object(
        Bucket=bucket, Key=key, Body=content,
        ContentType=ct,
        ACL=s3_acl,
        Metadata=metadata,
        **extra_kwargs,
    )
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
            "metadata":     h.get("Metadata", {}),
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
