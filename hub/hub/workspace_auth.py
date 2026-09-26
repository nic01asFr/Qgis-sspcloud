"""Authentification hub -> workspace (audit securite des acces, SEC-2).

Constate le 2026-09-26 : le pod workspace (API 8080, serveur MCP 8100, noVNC
6080) n'authentifiait aucun appel. `/api/execute` execute du Python
arbitraire ; x11vnc tournait en `-nopw`. Tout pod capable de joindre le
Service `qgis-workspace-<user>` pilotait donc QGIS et lisait les fichiers.

Le workspace accepte desormais, hors boucle locale :
  - `X-Workspace-Token: <jeton>` ; le jeton est derive de HUB_API_KEY
    (HMAC-SHA256, libelle `qgis-workspace-v1`), que le hub ET le workspace
    recoivent deja du meme Secret `qgis-hub-apikey` : aucun nouveau secret a
    creer, a stocker ou a faire tourner. `WORKSPACE_TOKEN`, s'il est defini
    des deux cotes, remplace la derivation ;
  - `Authorization: Bearer <HUB_API_KEY>`, que les appels directs du hub
    (execute_python, televersement, audit) envoient deja ;
  - pour noVNC (websockify, plugin BasicHTTPAuth) : `Authorization: Basic`
    avec l'utilisateur `hub` et le jeton derive.

Le hub ajoute le jeton sur tout ce qu'il relaie vers le workspace (proxy
`/mcp`, `/api/files`, `/api/upload`, `/workspace/vnc/*`), en ecrasant toute
valeur fournie par le client. Le calcul est identique a celui de
BigQgisMCP `src/workspace_auth.py` : les deux doivent rester alignes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

ENTETE_JETON = "X-Workspace-Token"
_LIBELLE = b"qgis-workspace-v1"
UTILISATEUR_VNC = "hub"


def jeton_workspace() -> str:
    """Jeton attendu par le workspace, ou chaine vide si non calculable."""
    explicite = os.environ.get("WORKSPACE_TOKEN", "").strip()
    if explicite:
        return explicite
    cle = os.environ.get("HUB_API_KEY", "").strip()
    if not cle:
        return ""
    return hmac.new(cle.encode("utf-8"), _LIBELLE, hashlib.sha256).hexdigest()


def entetes_workspace() -> dict[str, str]:
    """En-tete a ajouter a toute requete relayee vers le workspace."""
    jeton = jeton_workspace()
    return {ENTETE_JETON: jeton} if jeton else {}


def entete_basic_vnc() -> dict[str, str]:
    """En-tete Authorization Basic attendu par websockify (noVNC)."""
    jeton = jeton_workspace()
    if not jeton:
        return {}
    brut = f"{UTILISATEUR_VNC}:{jeton}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(brut).decode("ascii")}


def sans_entete_client(entetes: dict[str, str]) -> dict[str, str]:
    """Retire un eventuel jeton fourni par le client (on ne le relaie jamais)."""
    return {k: v for k, v in entetes.items() if k.lower() != ENTETE_JETON.lower()}
