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

_KINDS = {"storymap", "flux", "recipe", "dataset", "pdf"}
_KIND_EXT = {
    "storymap": "html",
    "flux":     "qgz",
    "recipe":   "yaml",
    "dataset":  "gpkg",
    "pdf":      "pdf",
}
_KIND_CONTENT_TYPE = {
    "storymap": "text/html; charset=utf-8",
    "flux":     "application/octet-stream",
    "recipe":   "application/x-yaml; charset=utf-8",
    "dataset":  "application/geopackage+sqlite3",
    "pdf":      "application/pdf",
}

# Chemin du secret passerelle (creds long-lived service-side)
_SECRET_NAME = os.getenv("PASSERELLE_S3_SECRET", "passerelle-s3-creds")
_S3_PREFIX = "qgis-workspace/published"
_CATALOG_PREFIX = "qgis-workspace/catalog"


# ── Lecture credentials passerelle (cached, refresh hourly) ──────────────────

_creds_cache: dict[str, Any] = {"ts": 0, "data": None}
_CREDS_TTL = 3600  # re-lit le secret toutes les heures (token STS rotation)


def _read_passerelle_s3_creds() -> dict[str, str]:
    now = time.time()
    if _creds_cache["data"] and (now - _creds_cache["ts"]) < _CREDS_TTL:
        return _creds_cache["data"]

    r = subprocess.run(
        ["kubectl", "get", "secret", _SECRET_NAME, "-o", "json"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Secret {_SECRET_NAME} inaccessible: {r.stderr[:200]}")
    data = json.loads(r.stdout)["data"]
    decoded = {k: base64.b64decode(v).decode() for k, v in data.items()}
    _creds_cache["data"] = decoded
    _creds_cache["ts"] = now
    return decoded


import boto3  # noqa: E402 — top-level pour que _S3_AVAILABLE détecte l'absence


def _get_s3_client():
    """Renvoie (client, bucket, endpoint)."""
    creds = _read_passerelle_s3_creds()
    endpoint = creds.get("AWS_S3_ENDPOINT", "https://minio.lab.sspcloud.fr").rstrip("/")
    if not endpoint.startswith("http"):
        endpoint = "https://" + endpoint
    bucket = creds["SSPCLOUD_BUCKET"]
    client = boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=creds["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=creds["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=creds.get("AWS_SESSION_TOKEN"),
        region_name=creds.get("AWS_DEFAULT_REGION", "us-east-1"),
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
            study_id: str | None = None) -> dict:
    """
    Publie un livrable. Retourne {url, key, kind, slug, size, study_id}.
    Pose ACL=public-read pour rendre l'URL directement consultable.

    Phase 13 : `study_id` lie la publication à l'étude qui l'a produite.
    Sert pour la traçabilité (provenance des données) et l'UI desk
    (grouper les publications par étude).
    """
    client, bucket, endpoint = _get_s3_client()
    key = s3_key(owner, kind, slug)
    ct = content_type or _KIND_CONTENT_TYPE.get(kind, "application/octet-stream")

    metadata = {
        "owner":    owner,
        "kind":     kind,
        "slug":     _safe_slug(slug),
        "published-at": str(int(time.time())),
    }
    if study_id:
        metadata["study-id"] = study_id

    client.put_object(
        Bucket=bucket, Key=key, Body=content,
        ContentType=ct,
        ACL="public-read",
        Metadata=metadata,
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
    }
    if study_id:
        info["study_id"] = study_id
    # MAJ catalogue user
    _update_catalog(owner, info)
    return info


def read(owner: str, kind: str, slug: str) -> bytes | None:
    """Récupère le contenu d'une publication. None si absent."""
    client, bucket, _ = _get_s3_client()
    key = s3_key(owner, kind, slug)
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()
    except client.exceptions.NoSuchKey:
        return None
    except Exception as exc:
        log.warning("S3 read failed %s/%s: %s", owner, slug, exc)
        return None


def head(owner: str, kind: str, slug: str) -> dict | None:
    """Métadonnées d'une publication sans télécharger le body."""
    client, bucket, endpoint = _get_s3_client()
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
    client, bucket, _ = _get_s3_client()
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
    client, bucket, endpoint = _get_s3_client()
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
    client, bucket, _ = _get_s3_client()
    try:
        obj = client.get_object(Bucket=bucket, Key=_catalog_key(owner))
        return json.loads(obj["Body"].read())
    except client.exceptions.NoSuchKey:
        return []
    except Exception:
        return []


def _save_catalog(owner: str, items: list[dict]) -> None:
    client, bucket, _ = _get_s3_client()
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
