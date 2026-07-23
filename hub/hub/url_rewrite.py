"""hub.url_rewrite — Réécriture URLs workspace pod-interne vers hub public.

Historique : le regex + logique de rewrite existait deja cote agent LLM
(agent/agent/qgis_agent.py:_rewrite_workspace_urls, ajoute 2026-07-11
pour bug "lien Windows mort" quand un tool MCP retournait une URL
`http://localhost:8080/api/...` que l'utilisateur cliquait dans l'UI
chat).

Mais le proxy MCP hub (main.py:mcp_auto_session -> _proxy_request) ne
faisait PAS cette réécriture pour les clients MCP externes (Claude
Desktop, Cursor, Cline, custom MCP HTTP Streamable). Ces clients
recevaient donc `http://localhost:8080/api/upload` directement, URL
inutilisable depuis un PC user (le pod workspace n'expose pas
localhost:8080 sur son ingress public).

Ce module fournit la logique en source unique cote hub. Le proxy
MCP hub l'applique sur les reponses `tools/call` bufferisees (voir
main.py:_proxy_request patch 2026-07-23). L'agent conserve son
propre helper local en défense-in-depth (regex identique, idempotent
= second passage sur URL deja publique est un no-op ~1ms).

Regex ciblé : DEUX endpoints workspace pod-interne exposés par
BigQgisMCP api_server.py sur localhost:8080 :
    - /api/files/{path}  (download GET)  -> reécrit vers {hub}/files/{path}
    - /api/upload         (upload POST)  -> reécrit vers {hub}/api/upload

Le hub proxifie ces 2 chemins (main.py:9016 pour /api/upload,
main.py:9095 pour /files/{path}) avec auth OIDC/Bearer et session
resolution automatique.
"""
from __future__ import annotations

import re

# Match localhost ou 127.0.0.1 avec n'importe quel port + suffixe /api/files/X
# ou /api/upload. Le caractere ' est inclus dans la classe negative pour
# supporter les payloads JSON echappes.
_WORKSPACE_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1):\d+(/api/(?:files/[^\s\"'<>)]+|upload))"
)


def rewrite_workspace_urls(payload: str, hub_url: str) -> str:
    """Remplace URLs pod-interne `localhost:PORT/api/...` par URL hub publique.

    - `/api/files/X`  -> `{hub_url}/files/X`   (proxy hub /files/{path})
    - `/api/upload`   -> `{hub_url}/api/upload` (proxy hub /api/upload)

    Idempotent : le regex ne matche pas les URLs deja reecrites (hub public
    n'est pas localhost). Un second appel est un no-op ~1ms.

    Fail-safe : si payload vide ou hub_url vide, retourne payload inchange
    (evite de casser le proxy si _HUB_URL n'est pas encore initialise au
    boot du pod).

    Args:
        payload: str contenant potentiellement des URLs a reecrire.
                 Peut etre le body d'une reponse JSON tools/call, un
                 event SSE, un message texte, un log.
        hub_url: URL publique du hub sans trailing slash
                 (ex: https://user-nicolaslaval-qgis.user.lab.sspcloud.fr).

    Returns:
        payload avec URLs reecrites, ou payload inchange si params invalides.
    """
    if not payload or not hub_url:
        return payload

    def _repl(m: "re.Match[str]") -> str:
        p = m.group(1)
        if p.startswith("/api/files/"):
            return f"{hub_url}/files/" + p[len("/api/files/"):]
        return f"{hub_url}{p}"

    return _WORKSPACE_URL_RE.sub(_repl, payload)
