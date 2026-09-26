"""
hub.publish.bundle - F16 Bundle ZIP autonome + signature Ed25519.

Sprint 1.4 Vague 1 Equipe B (2026-07-05). Livre un ZIP autonome
consommable hors ligne :

- manifest.json        : Assembly Pydantic complet
- rendered/index.html  : rendu HTML final (avec composants)
- components/*.json    : manifest de chaque composant reference
- assets/              : illustrations, tuiles, GeoJSON, PDF...
- audit_chain.json     : integrity_hash + provenance complete
- integrity.txt        : integrity_hash + signature Ed25519 hex
- README.txt           : instructions de verification pour l'utilisateur

La signature Ed25519 sur `integrity_hash` fournit une chain-of-custody
tamper-evident : quiconque a la cle publique CEREMA peut verifier que
le hash n'a pas ete modifie.

Gestion de la cle privee :
- Env var `CEREMA_ED25519_PRIVATE_KEY` (raw hex 32 bytes ou base64), fournie
  par l'installation (chart : `publication.signingKey.existingSecret`).
- Sans cle valide, le bundle n'est PAS signe, et le dit (integrity.txt,
  README.txt). Il n'y a plus de cle de demonstration : jusqu'au 2026-09-26,
  l'absence de cle faisait signer avec `bytes(range(32))`, cle publique
  connue de tous. N'importe qui pouvait donc forger une signature « valide »
  sur un bundle modifie : l'auditabilite promise etait nulle (LIV-3).

Choix produit : Ed25519 plutot que RSA-PSS car cles courtes (32 bytes),
signatures courtes (64 bytes), verification rapide, pas de choix de courbe.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import os
import time
import zipfile
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

log = logging.getLogger("hub.publish.bundle")

_ENV_PRIV_KEY = "CEREMA_ED25519_PRIVATE_KEY"


class SignatureIndisponible(RuntimeError):
    """Aucune cle de signature fournie par l'installation."""


def _load_private_key(priv_key_bytes: bytes | None = None) -> Ed25519PrivateKey | None:
    """Charge la cle Ed25519 fournie, ou None s'il n'y en a pas.

    Priorite :
    1. Argument `priv_key_bytes` (utilise par les tests).
    2. Env var CEREMA_ED25519_PRIVATE_KEY (raw hex 64 chars, ou base64).
    Aucun repli : sans cle valide, on ne signe pas.
    """
    if priv_key_bytes:
        return Ed25519PrivateKey.from_private_bytes(priv_key_bytes[:32])

    raw = os.getenv(_ENV_PRIV_KEY, "").strip()
    if raw:
        try:
            key_bytes = binascii.unhexlify(raw)
            if len(key_bytes) == 32:
                return Ed25519PrivateKey.from_private_bytes(key_bytes)
        except (binascii.Error, ValueError):
            pass
        try:
            key_bytes = base64.b64decode(raw)
            if len(key_bytes) == 32:
                return Ed25519PrivateKey.from_private_bytes(key_bytes)
        except (binascii.Error, ValueError):
            pass
        log.warning(
            "%s present mais format invalide (attendu : 32 octets en hex ou "
            "base64). Bundle NON signe.", _ENV_PRIV_KEY,
        )
        return None

    log.warning(
        "F16 bundle : aucune cle de signature (%s absente). Bundle NON signe. "
        "Fournir la cle via publication.signingKey.existingSecret du chart.",
        _ENV_PRIV_KEY,
    )
    return None


def sign_integrity(
    integrity_hash: bytes,
    priv_key_bytes: bytes | None = None,
) -> tuple[bytes, str]:
    """Signe `integrity_hash` avec Ed25519.

    Retourne (signature_64bytes, public_key_hex). Leve SignatureIndisponible
    si aucune cle n'est fournie : pas de signature de complaisance.
    """
    priv = _load_private_key(priv_key_bytes)
    if priv is None:
        raise SignatureIndisponible(
            f"Aucune cle de signature : definir {_ENV_PRIV_KEY}."
        )
    signature = priv.sign(integrity_hash)
    pub_hex = priv.public_key().public_bytes_raw().hex()
    return signature, pub_hex


def _readme_txt(slug: str, version_num: int, integrity_hash: str,
                pub_key_hex: str, signe: bool) -> str:
    if signe:
        bloc_signature = f"""SIGNATURE :
Algorithme       : Ed25519 (RFC 8032)
Cle publique hex : {pub_key_hex}

Verifier la signature :
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUB_HEX))
    pub.verify(SIG_BYTES, INTEGRITY_HASH.encode())
"""
    else:
        bloc_signature = (
            "SIGNATURE :\n"
            "[!] Ce bundle N'EST PAS SIGNE : l'installation n'a pas fourni de\n"
            "    cle de signature. Le hash d'integrite ci-dessus detecte une\n"
            "    modification accidentelle, mais n'authentifie pas l'emetteur.\n"
        )
    return f"""BUNDLE PUBLICATION QGIS-SSPCLOUD - F16 (Sprint 1.4)
======================================================

Slug           : {slug}
Version        : v{version_num}
Genere le      : {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}

CONTENU :
- manifest.json       -> Assembly Pydantic V0.1 (source de verite)
- rendered/index.html -> Rendu HTML autonome (ouvrable dans navigateur)
- components/*.json   -> Manifest de chaque composant reference
- assets/             -> Illustrations, tuiles, GeoJSON, PDF, etc.
- audit_chain.json    -> Chain-of-custody complete (D-FORMAT-008)
- integrity.txt       -> integrity_hash + signature Ed25519

INTEGRITE :
integrity_hash = {integrity_hash}

Ce hash est calcule sur la version canonique JSON du manifest
Assembly (D-FORMAT-008 2026-06-29). Toute modification de manifest.json
INVALIDE ce hash.

{bloc_signature}
Emis par : QGIS-SSPCloud Hub (CEREMA)
"""


def build_zip_bundle(
    manifest: dict[str, Any],
    rendered_html: str,
    audit_chain: dict[str, Any],
    components: dict[str, dict[str, Any]],
    assets: dict[str, bytes],
    version_num: int,
    slug: str,
    priv_key_bytes: bytes | None = None,
) -> bytes:
    """Construit le ZIP bundle et retourne les bytes.

    Arguments :
    - manifest       : dict de l'Assembly (Pydantic dump)
    - rendered_html  : contenu HTML rendu
    - audit_chain    : dict AuditChain avec integrity_hash
    - components     : {cid: component_manifest_dict}
    - assets         : {chemin_relatif: bytes}
    - version_num    : version de la publication (F17)
    - slug           : slug de la publication
    - priv_key_bytes : override cle Ed25519 (tests) sinon env
    """
    integrity_hash = (
        audit_chain.get("integrity_hash")
        or audit_chain.get("signed_hash")
        or "sha256:unknown"
    )
    priv = _load_private_key(priv_key_bytes)
    signe = priv is not None
    if signe:
        signature = priv.sign(integrity_hash.encode("utf-8"))
        pub_hex = priv.public_key().public_bytes_raw().hex()
        integrity_txt = (
            f"INTEGRITY_HASH: {integrity_hash}\n"
            f"SIGNATURE_ALGO: Ed25519\n"
            f"SIGNATURE_HEX:  {signature.hex()}\n"
            f"PUBLIC_KEY_HEX: {pub_hex}\n"
            f"SIGNED_AT_UTC:  {int(time.time())}\n"
            f"SIGNED:         true\n"
        )
    else:
        pub_hex = ""
        integrity_txt = (
            f"INTEGRITY_HASH: {integrity_hash}\n"
            f"SIGNATURE_ALGO: none\n"
            f"SIGNED:         false\n"
            f"GENERATED_AT_UTC: {int(time.time())}\n"
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        )
        z.writestr("rendered/index.html", rendered_html)
        z.writestr(
            "audit_chain.json",
            json.dumps(audit_chain, ensure_ascii=False, indent=2, sort_keys=True),
        )
        z.writestr("integrity.txt", integrity_txt)
        z.writestr(
            "README.txt",
            _readme_txt(slug, version_num, integrity_hash, pub_hex, signe),
        )
        for cid, comp in (components or {}).items():
            z.writestr(
                f"components/{cid}.json",
                json.dumps(comp, ensure_ascii=False, indent=2, sort_keys=True),
            )
        for path, blob in (assets or {}).items():
            safe_path = path.replace("\\", "/").lstrip("/")
            if ".." in safe_path.split("/"):
                log.warning("F16 asset path unsafe, skip : %s", path)
                continue
            z.writestr(f"assets/{safe_path}", blob)
    return buf.getvalue()


def verify_signature(
    integrity_hash: bytes,
    signature: bytes,
    pub_key_hex: str,
) -> bool:
    """Verifie la signature Ed25519. True si valide."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_key_hex))
        pub.verify(signature, integrity_hash)
        return True
    except Exception:
        return False
