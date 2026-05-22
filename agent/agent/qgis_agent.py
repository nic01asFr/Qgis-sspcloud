"""
agent.qgis_agent — Agent QGIS spécialisé via Agno + SSPCloud LLM.

Architecture :
  - Agno Agent : orchestration LLM + outils MCP
  - SSPCloud LLM API : Gemma4/Qwen3 (compatible OpenAI)
  - MCP Client : appels vers le hub /mcp
  - Mémoire : SQLite PVC via memory.py
  - Profil : system prompt adapté via profile_manager
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import AsyncGenerator

import httpx

from agent import memory

log = logging.getLogger("agent.qgis_agent")

# ── Config SSPCloud LLM ────────────────────────────────────────────────────────
_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api")
_LLM_API_KEY  = os.getenv("LLM_API_KEY", "")

# Modèle par profil :
# qwen3-6-35b-moe : function calling natif, thinking séparé → optimal pour tools
# gemma4-26b-moe  : vision base64 → optimal pour validation images GeoAI
_MODEL_BY_PROFILE = {
    "geoai_analyst": "gemma4-26b-moe",   # vision pour validation détections
    "default":        "qwen3-6-35b-moe", # tool calling fiable
}

def _get_model(profile_id: str) -> str:
    return _MODEL_BY_PROFILE.get(profile_id, _MODEL_BY_PROFILE["default"])

# ── Config Hub MCP ─────────────────────────────────────────────────────────────
_ONYXIA_USER = os.getenv("ONYXIA_USER", "")
_HUB_URL     = (
    os.getenv("HUB_URL")
    or (f"https://user-{_ONYXIA_USER}-qgis-mcp-bridge.user.lab.sspcloud.fr"
        if _ONYXIA_USER else "")
)
_HUB_KEY  = os.getenv("HUB_API_KEY", os.getenv("QGIS_API_KEY", ""))

# ── Système de profils ─────────────────────────────────────────────────────────
_PROFILES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "qgis-mcp-hub", "api", "hub", "profiles")


def _load_profile_prompt(profile_id: str) -> str:
    """Charge le system prompt d'un profil YAML."""
    try:
        import yaml
        profiles_path = os.getenv("PROFILES_DIR", _PROFILES_DIR)
        f = os.path.join(profiles_path, f"{profile_id}.yaml")
        if os.path.exists(f):
            with open(f) as fh:
                p = yaml.safe_load(fh)
            return p.get("agent_system_prompt", "")
    except Exception as e:
        log.warning("Profil %s non chargé: %s", profile_id, e)
    return ""


# ── Outils MCP disponibles ─────────────────────────────────────────────────────

async def _get_mcp_tools(profile_id: str = "standard") -> list[dict]:
    """Récupère la liste des outils MCP du hub, filtrée selon le profil."""
    if not _HUB_URL or not _HUB_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_HUB_URL}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": f"Bearer {_HUB_KEY}"},
            )
            data = resp.json()
            tools = data.get("result", {}).get("tools", [])
            log.info("MCP tools disponibles: %d", len(tools))
            return tools
    except Exception as e:
        log.warning("MCP tools non récupérés: %s", e)
        return []


def _extract_error_signature(tool_result: str) -> str | None:
    """Extrait une signature fuzzy d'erreur d'un résultat MCP, ou None.

    Sert à détecter les boucles : on neutralise les variables (types quoted,
    chiffres, paths) pour reconnaître "même classe d'erreur" même quand
    l'agent essaie des variantes.
    Ex: « unexpected type 'int' » et « unexpected type 'list' » → même sig.
    """
    if not tool_result:
        return None
    lower = tool_result.lower()
    error_markers = ('"error"', '"success": false', 'traceback', 'exception')
    if not any(m in lower for m in error_markers):
        return None
    try:
        data = json.loads(tool_result)
    except Exception:
        data = None
    raw = ""
    if isinstance(data, dict):
        err = data.get("error") or ""
        if isinstance(err, str) and err:
            raw = err.splitlines()[0]
    if not raw:
        raw = tool_result.splitlines()[0]
    # Fuzzify : retire valeurs entre guillemets/apostrophes, nombres, paths
    fuzzy = re.sub(r"'[^']*'|\"[^\"]*\"", "X", raw)
    fuzzy = re.sub(r"\b\d+\b", "N", fuzzy)
    fuzzy = re.sub(r"/[^\s\"']+", "PATH", fuzzy)
    return fuzzy[:120]


async def _maybe_enrich_with_kb_hint(response: str) -> str:
    """Hook auto post-erreur : si la réponse tool contient une erreur, cherche
    dans la KB qgis_tips (vector_store source_type="qgis_tip") un tip dont le
    symptom matche sémantiquement. Si match ≥ 0.55, suffixe la réponse avec
    un bloc 💡 KB tip (pattern + note). Sinon retourne la réponse inchangée.

    Pourquoi côté backend, pas comme tool explicite : zéro latence ajoutée,
    zéro discipline LLM requise, l'agent voit error+fix dans le même payload.
    """
    if not response:
        return response
    error_text = ""
    # Détection erreur : la réponse tool est un mix `{json}\n--- Context: ...\n![img](data:...)`
    # (texte + screenshot inline). json.loads échouerait sur le mix global, donc
    # on extrait directement les champs "error" et "traceback" via regex sur les
    # 5000 premiers caractères (le JSON tool est toujours au début, l'image
    # base64 est volumineuse mais en fin).
    head = response[:5000]
    m_err = re.search(r'"error"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
    m_tb  = re.search(r'"traceback"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
    if m_err:
        err = m_err.group(1).encode().decode('unicode_escape', errors='ignore')
        tb = ""
        if m_tb:
            tb = m_tb.group(1).encode().decode('unicode_escape', errors='ignore')
        error_text = (err + " " + tb)[:600].strip()
    # Fallback : si pas de JSON-like détecté mais "error" ou "traceback" dans head
    elif "error" in head.lower()[:200] or "traceback" in head.lower()[:200]:
        error_text = head[:600].strip()

    if not error_text:
        return response

    try:
        from agent import vector_store
        hits = await vector_store.search(
            query=error_text, top_k=1, source_type="qgis_tip",
        )
    except Exception:
        return response

    if not hits:
        return response
    h = hits[0]
    if h.get("similarity", 0.0) < 0.55:
        return response

    meta = h.get("metadata") or {}
    title = meta.get("title") or h.get("source_id") or "tip"
    pattern = meta.get("pattern", "")
    note = meta.get("note", "")

    # Hint avec DIRECTIVE IMPÉRATIVE : l'agent doit appliquer le pattern et
    # retenter SILENCIEUSEMENT. L'utilisateur final n'est PAS codeur — il ne
    # doit pas voir l'erreur Python technique ni le hint. L'agent corrige en
    # background et passe à la suite comme si l'erreur n'avait pas existé.
    hint = (
        f"\n\n💡 [KB AUTO-CORRECTION — INSTRUCTION INTERNE, ne pas narrer à l'user]"
        f"\nTip pertinent : **{title}**"
        f"\nPattern à appliquer immédiatement :\n```python\n{pattern}\n```"
    )
    if note:
        hint += f"\nNote : {note}"
    hint += (
        f"\n\n⚠️ ACTION REQUISE : applique ce pattern et RETENTE tout de suite. "
        f"N'explique PAS l'erreur technique à l'user (il n'est pas codeur). "
        f"Continue silencieusement, comme si la 1ère tentative n'avait pas eu lieu."
    )
    return response + hint


async def _call_mcp_tool(tool_name: str, arguments: dict, username: str = "user") -> str:
    """Wrapper : appelle _call_mcp_tool_raw + enrichit la réponse via la KB
    qgis_tips si une erreur est détectée (hook auto post-erreur)."""
    response = await _call_mcp_tool_raw(tool_name, arguments, username)
    return await _maybe_enrich_with_kb_hint(response)


async def _call_mcp_tool_raw(tool_name: str, arguments: dict, username: str = "user") -> str:
    """Appelle un outil. Court-circuite les outils natifs (mémoire) côté agent
    avant de tenter le MCP hub QGIS."""
    # Outils natifs côté agent — n'appellent pas le hub MCP.
    if tool_name in ("memory_search", "memory_similar"):
        try:
            from agent import vector_store
            query    = arguments.get("query", "")
            top_k    = int(arguments.get("top_k", 5))
            # memory_similar : forcé sur insights (cf. tool description).
            if tool_name == "memory_similar":
                src_type = "insight"
            else:
                src_type = arguments.get("source_type")
            uname    = username
            results  = await vector_store.search(
                query=query, top_k=top_k,
                source_type=src_type, username=uname,
            )
            # Présentation compacte pour le LLM
            return json.dumps({
                "query": query,
                "count": len(results),
                "results": [
                    {
                        "similarity":  round(r["similarity"], 3),
                        "source_type": r["source_type"],
                        "text":        r["text"][:300],
                        "metadata":    r.get("metadata"),
                    }
                    for r in results
                ],
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"memory_search fail: {e}"})

    if not _HUB_URL or not _HUB_KEY:
        return json.dumps({"error": "Hub non configuré"})
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{_HUB_URL}/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id":      str(uuid.uuid4()),
                    "method":  "tools/call",
                    "params":  {"name": tool_name, "arguments": arguments},
                },
                headers={"Authorization": f"Bearer {_HUB_KEY}"},
            )
            data = resp.json()
            result = data.get("result", {})
            content = result.get("content", [])
            if content and isinstance(content, list):
                # Concatène texte + image (en data URL markdown) — l'image
                # est rendue inline par marked.parse côté chat.
                parts: list[str] = []
                for c in content:
                    ctype = c.get("type")
                    if ctype == "text":
                        parts.append(c.get("text", ""))
                    elif ctype == "image":
                        b64 = c.get("data", "")
                        mime = c.get("mimeType", "image/png")
                        if b64:
                            parts.append(
                                f"![{tool_name}](data:{mime};base64,{b64})"
                            )
                joined = "\n".join(p for p in parts if p)
                # Réécriture des URLs internes au pod workspace → URL hub
                # publique. Le serveur MCP du workspace ignore qu'il est
                # derrière un proxy ; il retourne `localhost:8080/api/files/X`
                # qui n'est joignable que depuis l'intérieur du pod. Le hub
                # expose `/files/{path}` qui proxy vers le service K8s.
                joined = re.sub(
                    r"http://localhost:\d+/api/files/",
                    f"{_HUB_URL}/files/",
                    joined,
                )
                return joined
            return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Conversion outils MCP → format OpenAI function calling ────────────────────

def _mcp_tool_to_openai(tool: dict) -> dict:
    """Convertit un outil MCP en format OpenAI function calling."""
    schema = tool.get("inputSchema", {"type": "object", "properties": {}})
    return {
        "type": "function",
        "function": {
            "name":        tool["name"],
            "description": tool.get("description", ""),
            "parameters":  schema,
        }
    }


# ── Outils natifs côté agent (mémoire sémantique) ────────────────────────
# Dispatchés localement par _call_mcp_tool, donc 0 latence réseau, et l'agent
# voit la mémoire comme n'importe quel autre tool.
# Tools dont l'appel laisse une trace mesurable dans le projet QGIS courant
# (couche ajoutée/retirée, style modifié, géoprocessing, etc.). Servent de
# trigger pour l'auto-save post-turn de l'étude active. Lecture-seule
# (get_*, list_*, export_*) volontairement exclus.
_MUTATING_TOOLS: frozenset[str] = frozenset({
    "smart_load",
    "add_layer",
    "add_from_catalog",
    "remove_layer",
    "set_layer_style",
    "set_layer_visibility",
    "run_processing",
    "run_recipe",
    "execute_python",
    "apply_layout_template",
    "set_study_zone",
    "new_project",
    "open_project",
    "save_project",
    "upload_file",
    "zoom_to",
})


_NATIVE_MEMORY_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": (
                "Recherche sémantique dans la mémoire (messages passés, insights, "
                "mémoire structurée). À utiliser SEULEMENT si le 'Rappel sémantique "
                "automatique' déjà présent dans ton system prompt ne contient pas "
                "l'information précise dont tu as besoin (valeur exacte, session "
                "antérieure particulière, détail manquant). N'appelle PAS en plus "
                "si le rappel auto suffit — tu gaspilles un tour LLM."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Requête en langage naturel.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Nombre de résultats à retourner (défaut 5).",
                        "default": 5,
                    },
                    "source_type": {
                        "type": "string",
                        "description": (
                            "Filtrer sur un type de source : 'message' "
                            "(échanges passés), 'insight' (préférences/habitudes "
                            "apprises), 'memory_doc' (mémoire structurée user). "
                            "Omettre pour chercher partout."
                        ),
                        "enum": ["message", "insight", "memory_doc"],
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_similar",
            "description": (
                "Variante de memory_search ciblée sur les insights métier "
                "(préférences, méthodes, zones). Mêmes règles : si le rappel "
                "auto présent dans ton prompt système couvre déjà la question, "
                "n'appelle pas. À réserver aux cas où il faut sonder largement "
                "le profil utilisateur."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 3},
                },
                "required": ["query"],
            },
        },
    },
]


# ── Agent principal ────────────────────────────────────────────────────────────

class QGISAgent:
    """
    Agent QGIS spécialisé.
    Orchestre LLM SSPCloud + outils MCP + mémoire long-terme.
    """

    def __init__(self, username: str, session_id: str, profile_id: str = "standard"):
        self.username   = username
        self.session_id = session_id
        self.profile_id = profile_id
        self._tools_cache: list[dict] | None = None

    async def _get_tools(self) -> list[dict]:
        if self._tools_cache is None:
            mcp_tools = await _get_mcp_tools(self.profile_id)
            self._tools_cache = [_mcp_tool_to_openai(t) for t in mcp_tools]
            # Outils natifs mémoire : exposés à l'agent comme n'importe quel
            # tool MCP. Le dispatch local court-circuite le hub QGIS.
            self._tools_cache.extend(_NATIVE_MEMORY_TOOLS)
        return self._tools_cache

    async def set_profile(self, new_profile_id: str) -> None:
        """
        Switch en cours de conversation. Recharge les tools + system prompt
        au prochain turn. Réinitialise le cache.
        """
        if new_profile_id == self.profile_id:
            return
        self.profile_id = new_profile_id
        self._tools_cache = None  # forcer rechargement

    _SWITCH_INSTRUCTIONS = (
        "\n\n— PROFIL DYNAMIQUE —\n"
        "Tu as un profil actuel adapté à un type de tâche. Tu peux changer "
        "de profil en cours de conversation si la demande du user relève "
        "d'un autre champ. Profils disponibles : standard, geoai_analyst, "
        "risk_analyst, db_analyst, recipe_creator, storymap_creator, "
        "map_composer, guided_tour. Pour switcher, inclus dans ta réponse "
        "(une seule fois, en début ou fin) :\n"
        "<switch_profile>NOM_PROFIL</switch_profile>\n"
        "Le système l'intercepte, ne montre rien à l'user, et applique le "
        "switch pour les turns suivants. À utiliser avec parcimonie : seulement "
        "quand la demande change clairement de nature."
    )

    _REMEMBER_INSTRUCTIONS = (
        "\n\n— MÉMOIRE LONG TERME —\n"
        "Quand l'user te révèle une préférence durable, un fait stable sur lui, "
        "son métier, sa zone d'étude habituelle, ou un style cartographique "
        "récurrent, tu peux le mémoriser pour les conversations futures. "
        "Inclus dans ta réponse (caché à l'user) :\n"
        "<remember>clé:valeur</remember>\n"
        "Exemples :\n"
        "  <remember>preferred_zone:Le Lavandou</remember>\n"
        "  <remember>metier:chargé d'études risques inondations</remember>\n"
        "  <remember>style_pref:palette YlOrRd pour les chiffres clés</remember>\n"
        "À utiliser parcimonieusement : seulement quand l'info est explicitement "
        "ou très clairement durable. L'user pourra voir/effacer ces insights "
        "via le portail."
    )

    async def _fetch_active_study_context(self):
        """Récupère l'étude active + treatments depuis le hub.
        Utilise les constantes module qui ont résolu les fallbacks (URL
        dérivée d'ONYXIA_USER, clé QGIS_API_KEY si HUB_API_KEY absente)."""
        import httpx as _httpx
        if not (_HUB_URL and _HUB_KEY):
            return None, None
        headers = {"Authorization": f"Bearer {_HUB_KEY}"}
        try:
            async with _httpx.AsyncClient(timeout=5) as c:
                ra = await c.get(f"{_HUB_URL}/studies/active", headers=headers)
                if ra.status_code != 200 or not ra.json():
                    return None, None
                study = ra.json()
                rt = await c.get(
                    f"{_HUB_URL}/studies/{study['id']}/treatments",
                    headers=headers, params={"limit": 30},
                )
                treats = rt.json().get("events", []) if rt.status_code == 200 else []
                return study, treats
        except Exception as exc:
            log.warning("Fetch active study failed: %s", exc)
            return None, None

    async def _autosave_active_study(self) -> None:
        """
        Post-turn hook : si un tool modifiant a tourné, appelle l'endpoint
        hub `/studies/{active}/save` pour persister le projet QGIS sur le
        PVC à `/data/studies/{sid}/project.qgz`. Fire-and-forget — toute
        erreur est avalée (un crash de save ne doit pas remonter à l'user).

        On utilise les constantes module `_HUB_URL` / `_HUB_KEY` qui ont
        déjà résolu le fallback `QGIS_API_KEY` et l'URL dérivée d'ONYXIA_USER.
        """
        import httpx as _httpx
        if not (_HUB_URL and _HUB_KEY):
            log.debug("Auto-save skip : hub non configuré (url=%r key=%r)",
                      bool(_HUB_URL), bool(_HUB_KEY))
            return
        headers = {"Authorization": f"Bearer {_HUB_KEY}"}
        try:
            async with _httpx.AsyncClient(timeout=10) as c:
                ra = await c.get(f"{_HUB_URL}/studies/active", headers=headers)
                if ra.status_code != 200 or not ra.json():
                    log.debug("Auto-save skip : pas d'étude active (status=%d)",
                              ra.status_code)
                    return
                sid = ra.json().get("id")
                if not sid:
                    return
                rs = await c.post(f"{_HUB_URL}/studies/{sid}/save", headers=headers)
                if rs.status_code == 200:
                    log.info("Auto-save étude %s OK", sid)
                else:
                    log.warning("Auto-save étude %s status=%d body=%s",
                                sid, rs.status_code, rs.text[:200])
        except Exception as exc:
            log.warning("Auto-save étude fail : %s", exc)

    async def _fetch_project_state(self) -> dict | None:
        """L2 enrichi : récupère l'état du projet QGIS courant.

        Liste des layers chargés, zone d'étude active (variables projet),
        CRS du projet, etc. C'est ce que l'agent VOIT pour décider s'il faut
        recharger ou réutiliser des données existantes.

        Best-effort : si l'appel échoue (workspace endormi, MCP injoignable),
        renvoie None et l'agent fonctionne sans cette info.
        """
        try:
            result = await _call_mcp_tool("get_project_info", {})
            if not result:
                return None
            data = json.loads(result)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            log.warning("Fetch project state failed: %s", exc)
            return None

    # Cheat-sheet PyQGIS minimal injecté dans tous les profils. Évite que
    # l'agent boucle sur les pièges classiques (format EXTENT, etc.).
    # À enrichir au fil des erreurs observées en production.
    _QGIS_ESSENTIALS = """
# ⛔ AVANT TOUT execute_python — 5 RÉFLEXES OBLIGATOIRES

1. ⚙️ **Algo natif d'abord** : avant 30 lignes de PyQGIS, demande-toi
   « existe-t-il un `native:*` ? » Si tu hésites → `search_algorithms("mot-clé")`.
   Top 5 essentiels pour 80% des cas :
   - `native:countpointsinpolygon` — densité / count par maille
   - `native:creategrid` — maillage rectangulaire/hexa
   - `native:joinattributestable` — jointure attributaire
   - `native:zonalstatistics` — stats raster par zone
   - `native:fieldcalculator` — ajout/transformation champ

2. 📖 **Recipes d'abord** : `list_recipes()` avant de tout coder.
   `densite_bati`, `occupation_sol`, `risque_inondation`, etc. existent
   et sont validées — `run_recipe(id, params)` plutôt que ré-écrire.

2bis. 📚 **CATALOGUE d'abord — JAMAIS d'URL externe inventée** :
   Pour charger des données : `list_datasources()` puis `smart_load(id)`
   ou `add_from_catalog(id)`. Le catalogue contient les sources validées
   pour SSPCloud (IGN Géoplateforme, Géorisques, OSM via WFS officiel,
   DVF, BD TOPO, Corine Land Cover…).

   Si le catalogue ne contient PAS exactement ta clé (ex: "quartiers") :
   - ✅ ÉLARGIS la recherche : `list_datasources()` complet, regarde
     les sources proches (limites administratives, IRIS, OSM places…)
   - ✅ Si toujours rien : DEMANDE à l'user quelle source il préfère,
     OU propose 2-3 alternatives **présentes dans le catalogue**
   - ❌ NE JAMAIS inventer une URL externe (overpass.foo.bar, etc.).
     Les LLM hallucinent des mirrors qui n'existent pas → boucle d'erreurs.

   Endpoints externes autorisés (en dernier recours, si vraiment nécessaire) :
   - `https://overpass-api.de/api/interpreter` (Overpass officiel)
   - `https://overpass.openstreetmap.fr/api/interpreter` (mirror OSM France)
   - `https://geo.api.gouv.fr/*` (API officielle communes/INSEE)
   - `https://data.geopf.fr/*` (Géoplateforme IGN)
   Format Overpass : **GET avec data URL-encoded**, pas POST avec body brut
   (sinon 406 Apache). Toujours timeout=25 dans le QL.

   Sources connues pour quartiers/sub-communes :
   - IRIS INSEE via `https://geo.api.gouv.fr/communes/{insee}?fields=contour`
     ou couche `iris_2024` Géoplateforme (échelle infra-communale officielle)
   - OSM `place=quarter` ou `boundary=administrative admin_level=10`
     via Overpass officiel
   - Open Data municipal (data.marseille.fr, opendata.lyon.fr, etc.)

3. 🪤 **Pièges PyQGIS** — si `execute_python` est inévitable :
   - **JAMAIS** `int(feat["champ"])` direct (QVariant trap) →
     `v = feat["champ"]; if v is not None: v = int(v)`
   - **Singulier** : `dataProvider().addAttribute(field)` (PAS addAttributeS)
   - Imports explicites : `from qgis.core import (QgsProject, QgsVectorLayer,
     QgsFeature, QgsField, QgsExpression)` — pas de `from ... import (Qgs,`
     tronqué ou `Qgs` non importé → `NameError` garanti

4. ⛔ **STOP après 2 échecs `execute_python` identiques** : pivote
   OBLIGATOIREMENT vers `run_processing` (algo natif) ou demande à l'user.
   PAS de 3e tentative Python brute sur le même thème d'erreur.

5. 💬 **Échec = communication CLAIRE et NON-TECHNIQUE** : si tu finis par
   échouer après plusieurs tentatives, dis « Je rencontre une difficulté
   technique sur X, j'essaie une autre approche » — JAMAIS de traceback,
   JAMAIS de nom de classe Python, JAMAIS « QVariant » à l'user. Il n'est
   PAS codeur.

5bis. 🚫 **PAS de phrases d'intention répétées** : annonce ton intention
   UNE SEULE FOIS en début de turn (ex: « Je calcule la densité puis je
   construis la storymap »), puis APPELLE le tool IMMÉDIATEMENT. Pas de
   « Je vais... » répété 5 fois sans agir. Une phrase = un acte.

6. 🤫 **Mode thinking auto sur 💡 KB tip** : si tu reçois un bloc
   `💡 [KB AUTO-CORRECTION...]` après une erreur tool, c'est une instruction
   INTERNE invisible à l'user. APPLIQUE le pattern et RETENTE tout de suite,
   **silencieusement**. Ne dis pas « j'ai eu une erreur QVariant », ne
   commente pas l'erreur, ne mentionne pas le hint. L'user voit seulement
   le résultat final qui marche, comme si tu avais réussi du 1er coup.

# ⛔ RÈGLE ABSOLUE — AGIR D'ABORD, NARRER ENSUITE, FAITS UNIQUEMENT

## 1. AGIR via les outils — ne JAMAIS narrer en pseudo-code

Tu disposes d'outils MCP (set_study_zone, smart_load, run_processing,
execute_python, etc.). Tu DOIS les utiliser pour AGIR. Tu ne dois
JAMAIS écrire « voici le code que je vais exécuter » sous forme de
texte/pseudo-code — appelle l'outil DIRECTEMENT. Le user verra le
résultat dans la session.

Interdit :
- ❌ « Je vais maintenant exécuter ce script : ```python ...``` »
- ❌ « J'attends la confirmation pour ... » (n'attends rien, appelle l'outil)
- ❌ « Comme je n'ai pas la connaissance préalable, je vais d'abord ... »
  (ÇA SE FAIT EN APPELANT L'OUTIL, PAS EN LE DISANT)

Attendu :
- ✅ Appel d'outil immédiat (set_study_zone, smart_load, etc.)
- ✅ Brève phrase d'intention AVANT (« Je définis la zone... ») mais SANS
  écrire le code en texte ni attendre — appel direct du tool.

## 2. Pas d'invention factuelle géographique

Tu n'as PAS de connaissance géographique fine fiable sur la France.
Tu ne CONNAIS PAS la liste des quartiers de chaque arrondissement,
les monuments, les rues, les hameaux, les caractéristiques urbaines.

INTERDIT (zéro tolérance) :
- ❌ Citer le nom d'un quartier, lieu-dit, rue, monument sans l'avoir
  lu dans un résultat d'outil de cette session.
- ❌ Statistique démographique sans qu'elle vienne d'un outil.
- ❌ "Description" ou "caractérisation" qualitative basée sur connaissance
  pré-entraînée. Connaître code INSEE 13204 ne te dit RIEN sur
  les quartiers du 4e.

Quand on te demande de décrire une zone : retourne UNIQUEMENT les
champs du résultat de set_study_zone (name, code, bbox, centre). Pour
plus de contexte, propose explicitement de charger les couches OSM
places ou toponymes IGN via smart_load.

Chaque phrase de ta réponse doit pouvoir être tracée à un résultat
d'outil ou à la demande utilisateur. Sinon : SUPPRIME-la.

## 3. BBOX d'extraction ≠ emprise administrative

`smart_load` et la plupart des fournisseurs (WFS, BD TOPO) chargent les
entités dans le **rectangle d'enveloppe (bbox)** de la zone d'étude, PAS
dans le polygone administratif. Une bbox d'arrondissement inclut
inévitablement des entités situées HORS de la limite administrative
(coin diagonalement opposé, bords débordants).

INTERDIT (gros caillou, zéro tolérance) :
- ❌ « 50 110 bâtiments DANS le 4e arrondissement » (alors qu'on a chargé par bbox)
- ❌ Toute statistique présentée comme « pour la commune X » sans clip préalable
- ❌ Confondre étendue rectangulaire (bbox) et périmètre administratif

CORRECT :
- ✅ « 50 110 bâtiments dans l'emprise (bbox) du 4e arrondissement »
- ✅ « X entités dans la bbox de Marseille 4e (incluant des bords hors commune) »
- ✅ Si l'utilisateur veut un compte exact « dans la commune » : proposer
  explicitement de clipper d'abord avec `native:clip` contre l'emprise
  administrative (récupérable depuis `geo.api.gouv.fr` ou couche `admin_communes`)
  avant de donner un chiffre.

Règle pratique : avant de chiffrer « dans X », vérifier qu'on a fait un
clip. Sinon, dire « dans l'emprise / la bbox de X » ou ne pas chiffrer.

# Rappels PyQGIS essentiels

## EXTENT des algorithmes processing
Le param `EXTENT` est une CHAÎNE au format `"xmin,xmax,ymin,ymax [EPSG:NNNN]"`
(NB: l'ordre est xmin,xmax,ymin,ymax, PAS xmin,ymin,xmax,ymax). Tu peux aussi
passer un QgsRectangle ou un nom de couche dont l'étendue sera utilisée.

```python
# CORRECT : extent string
processing.run("native:creategrid", {
    "TYPE": 2,  # rectangle
    "EXTENT": "892748.95,896884.91,6246148.59,6250270.70 [EPSG:2154]",
    "HSPACING": 200, "VSPACING": 200,
    "HOVERLAY": 0,  "VOVERLAY": 0,
    "CRS": "EPSG:2154",
    "OUTPUT": "memory:grille_200m",
})

# CORRECT : extent depuis couche
ext = layer.extent()
extent_str = f"{ext.xMinimum()},{ext.xMaximum()},{ext.yMinimum()},{ext.yMaximum()} [EPSG:{layer.crs().postgisSrid()}]"
```

## Récupérer un layer chargé
```python
project = QgsProject.instance()
layer = project.mapLayersByName("Bâti BDTOPO - Marseille 4e")[0]
# OU par layer_id si tu l'as :
layer = project.mapLayer(layer_id)
```

## Joindre une couche au calque maillage (count features)
```python
result = processing.run("native:countpointsinpolygon", {
    "POLYGONS": grid_layer,    # ou layer_id, ou "memory:grille_200m"
    "POINTS":   bati_layer,    # NB: marche aussi pour des polygones (count)
    "WEIGHT":   "",
    "CLASSFIELD": "",
    "FIELD":    "NUMPOINTS",
    "OUTPUT":   "memory:densite",
})
density_layer = result["OUTPUT"]
QgsProject.instance().addMapLayer(density_layer)
```

## Style graduated (choroplèthe) — UTILISER LA FACTORY, JAMAIS LE CONSTRUCTEUR
```python
from qgis.core import (
    QgsGraduatedSymbolRenderer, QgsClassificationQuantile,
    QgsSymbol, QgsStyle,
)

# ÉTAPE 1 : symbole de base (polygone par défaut)
base = QgsSymbol.defaultSymbol(layer.geometryType())

# ÉTAPE 2 : ramp de couleurs (rouge YlOrRd standard)
ramp = QgsStyle.defaultStyle().colorRamp("YlOrRd")

# ÉTAPE 3 : renderer en quantile 5 classes — FACTORY .createRenderer()
renderer = QgsGraduatedSymbolRenderer("NUMPOINTS")
renderer.setClassificationMethod(QgsClassificationQuantile())
renderer.updateClasses(layer, 5)
renderer.updateColorRamp(ramp)

layer.setRenderer(renderer)
layer.triggerRepaint()
iface.mapCanvas().refresh()
```

⚠️ Le constructeur `QgsGraduatedSymbolRenderer(...)` n'accepte qu'un attribut
(str) en argument 1. Si tu vois `unexpected type 'int'` ou `'list'`, tu passes
mal les arguments — utilise la version ci-dessus.

## NULL en PyQGIS (piège récurrent)
`NULL` n'est PAS un littéral Python — utiliser `NULL` directement déclenche
`NameError: name 'NULL' is not defined`. Trois patterns selon le contexte :

```python
# 1) Attribut d'une feature
feat = next(layer.getFeatures())
if feat.attribute("hauteur") is None:  # OR NULL natif Python
    ...

# 2) Filter via expression QGIS (NULL fonctionne ici, c'est du SQL-like)
expr = QgsExpression('"hauteur" >= 30 AND "hauteur" IS NOT NULL')
req = QgsFeatureRequest(expr)
features = layer.getFeatures(req)

# 3) Type QVariant via from PyQt5.QtCore import QVariant
# Rare, utile quand on construit des features programmatiquement.
```

Pour compter avec gestion des NULL :
```python
total = layer.featureCount()
nulls = sum(1 for f in layer.getFeatures() if f.attribute("hauteur") is None)
valid = total - nulls
```

## Gestion des erreurs
Si tu rebondis 2× sur la même erreur, CHANGE D'APPROCHE — n'insiste pas.
Vérifie les types attendus (string vs liste vs object), demande confirmation
à l'utilisateur si nécessaire, ou propose une méthode alternative.

## Communication finale
Toujours conclure par un message en français résumant en LANGUE NATURELLE
ce qui a été fait, ce qui a échoué le cas échéant, et la prochaine étape
suggérée. NE LAISSE JAMAIS un traceback brut comme dernière sortie utilisateur.

## 3. Hypothèses ≠ faits — toujours étiqueter

Quand on te demande « que représente cela ? » ou « pourquoi ? » et que tu
n'as PAS vérifié l'explication via un outil, tu ne peux PAS lister une
catégorisation comme si elle était factuelle.

Mauvaise réponse :
> Les cellules vides correspondent à : espaces verts, infrastructures
> de transport, zones de servitudes, zones de pente accidentées.

(L'agent ne sait PAS lesquelles sont laquelle. C'est une LISTE de causes
possibles présentée comme une décomposition réelle. INTERDIT.)

Bonne réponse :
> Les cellules vides peuvent correspondre à des espaces non bâtis :
> parcs, voiries, terrains vagues, reliefs. Je n'ai PAS vérifié laquelle
> de ces catégories explique réellement chaque cellule.
> Je peux croiser avec la couche OSM landuse ou Corine Land Cover si tu
> veux une réponse vérifiée.

Pattern : « peut-être... possiblement... à vérifier... » au lieu de la
forme affirmative. Marqueur explicite « hypothèse non vérifiée ».

## Substitutions = à signaler explicitement
Si tu ne trouves PAS exactement ce qu'a demandé l'utilisateur (template
absent, paramètre indisponible, données partielles), tu DOIS le signaler
explicitement dans le message final. Exemples :

- Demande « A4 paysage » mais seul `a3_landscape` ou `a4_portrait`
  disponibles → utilise le plus proche ET DIS « J'ai utilisé A4 portrait
  faute d'A4 paysage disponible. Veux-tu A3 paysage ? »
- Demande données 2024 mais seules 2023 dispo → utilise 2023 ET signale.
- Demande analyse précise mais couche partielle → fait avec ce qui est
  dispo ET indique la limite des données.

La substitution silencieuse est BANNIE — elle trompe l'utilisateur sur
ce qu'il a réellement reçu.

## Rappel final
La règle absolue en tête de prompt s'applique à CHAQUE phrase de ta réponse.
Avant d'envoyer : relire mentalement, si une phrase contient un fait qui
ne vient pas d'un outil cette session, la supprimer.
"""

    async def _build_system_prompt(self, user_message: str | None = None) -> str:
        """
        System prompt = profil + cheat-sheet PyQGIS + couches mémoire 1+2+3
        + enrichers déterministes (si user_message fourni) + directives.

        Couche 1 (conversation) : géré par history dans messages[].
        Couche 2 (étude active + état projet QGIS) : fetched parallèle.
        Couche 3 (user permanent) : depuis SQLite local memory.
        Enrichers (Phase 9 étape A) : regex-based, asyncio.gather. Fournissent
        un "contexte enrichi" propre à la requête courante, AVANT que le LLM
        ne décide de ses tools.
        """
        profile_prompt = _load_profile_prompt(self.profile_id)
        if not profile_prompt:
            profile_prompt = (
                "Tu es un expert QGIS pour le CEREMA, spécialisé en analyse géospatiale. "
                "Tu as accès à des outils QGIS (set_study_zone, smart_load, execute_python, "
                "run_processing, export_pdf, export_web_map...). "
                "Utilise-les pour répondre aux demandes d'analyse spatiale."
            )

        # Tâches parallèles : couche 2 + état projet + enrichers (si user_msg)
        from agent import enrichers
        active_study_task  = asyncio.create_task(self._fetch_active_study_context())
        project_state_task = asyncio.create_task(self._fetch_project_state())
        # State partagé aux enrichers : ids des messages déjà en L1 pour que
        # memory_recall n'affiche pas en doublon ce que le LLM voit déjà.
        recent_ids = []
        try:
            recent = await memory.get_session_messages(self.session_id, limit=20)
            recent_ids = [m.get("id") for m in recent if m.get("id")]
        except Exception:
            pass
        enrich_state = {"recent_message_ids": recent_ids}
        enrich_task = (
            asyncio.create_task(enrichers.run_all(user_message, enrich_state))
            if user_message else None
        )

        active_study, active_treats = await active_study_task
        project_state = await project_state_task
        enrich_results = await enrich_task if enrich_task else []

        # Couches 2 + 3 assemblées dans memory.build_context_summary
        ctx = await memory.build_context_summary(
            self.username, self.session_id, self.profile_id,
            active_study=active_study,
            active_study_treatments=active_treats,
            project_state=project_state,
        )

        # Contexte enrichi spécifique à la requête (si applicable)
        enrich_block = enrichers.format_for_prompt(enrich_results) if enrich_results else ""
        enrich_section = f"\n\n{enrich_block}" if enrich_block else ""

        return (
            f"{profile_prompt}\n{self._QGIS_ESSENTIALS}\n\n{ctx}{enrich_section}"
            f"{self._SWITCH_INSTRUCTIONS}{self._REMEMBER_INSTRUCTIONS}"
        ).strip()

    async def chat_stream(
        self,
        user_message: str,
        history: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Génère une réponse en streaming avec appels d'outils MCP.
        Yield des chunks de texte + events d'outils.
        """
        # Sauvegarder le message user
        await memory.add_message(self.session_id, "user", user_message)

        system_prompt = await self._build_system_prompt(user_message=user_message)
        tools         = await self._get_tools()

        # Construire les messages
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history[-20:])  # derniers 20 messages pour contexte
        messages.append({"role": "user", "content": user_message})

        full_response = ""
        tool_calls_made = []

        # Boucle agent : LLM → tool calls → LLM → ...
        # 20 itérations = budget pour analyse multi-étapes + construction
        # livrable + publication dans le même turn (ex: storymap end-to-end).
        max_iterations = 20
        final_finish_reason = None
        for iteration in range(max_iterations):
            # Appel LLM
            model = os.getenv("LLM_MODEL", _get_model(self.profile_id))
            payload = {
                "model":      model,
                "messages":   messages,
                "stream":     True,
                "max_tokens": 4096,
            }
            if tools:
                payload["tools"] = tools
                # Laisser le modèle décider : auto sur 1ère itération,
                # puis auto sur les suivantes (évite boucle infinie)
                payload["tool_choice"] = "auto"

            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{_LLM_BASE_URL}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {_LLM_API_KEY}",
                        "Content-Type":  "application/json",
                    }
                ) as resp:
                    chunk_text     = ""
                    tool_call_data: dict[int, dict] = {}
                    finish_reason  = None

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            d = json.loads(data)
                            delta = d["choices"][0].get("delta", {})
                            finish_reason = d["choices"][0].get("finish_reason")

                            # Texte — ignorer reasoning_content (Qwen3 thinking séparé)
                            # Qwen3 : thinking dans delta.reasoning_content (champ séparé)
                            # Gemma4 : thinking dans delta.content (tokens <|channel>)
                            if delta.get("content"):
                                raw = delta["content"]
                                # Filtrer tokens thinking Gemma4 dans content
                                import re as _re
                                clean = _re.sub(r'<\|[^>]*>', '', raw)
                                clean = _re.sub(r'<[^>]*\|>', '', clean)
                                # Cacher les directives à l'user (appliquées en fin de turn)
                                clean = _re.sub(
                                    r'<switch_profile>[^<]*</switch_profile>',
                                    '', clean, flags=_re.IGNORECASE,
                                )
                                clean = _re.sub(
                                    r'<remember>[^<]*</remember>',
                                    '', clean, flags=_re.IGNORECASE,
                                )
                                if clean:
                                    chunk_text += clean
                                    full_response += clean
                                    yield clean
                            # reasoning_content (Qwen3) → on l'ignore (pensées internes)

                            # Tool calls (accumulation)
                            for tc in delta.get("tool_calls", []):
                                idx = tc["index"]
                                if idx not in tool_call_data:
                                    tool_call_data[idx] = {
                                        "id":       tc.get("id", ""),
                                        "type":     "function",
                                        "function": {"name": "", "arguments": ""},
                                    }
                                if tc.get("id"):
                                    tool_call_data[idx]["id"] = tc["id"]
                                fn = tc.get("function", {})
                                if fn.get("name"):
                                    tool_call_data[idx]["function"]["name"] += fn["name"]
                                if fn.get("arguments"):
                                    tool_call_data[idx]["function"]["arguments"] += fn["arguments"]
                        except Exception:
                            pass

            final_finish_reason = finish_reason
            # Pas de tool calls → fin du turn LLM.
            # NB : on n'utilise PAS finish_reason="stop" pour break car Gemma4
            # renvoie souvent "stop" même quand des tool_calls sont présents.
            # Seule la présence/absence de tool_call_data détermine la suite.
            if not tool_call_data:
                # Garde-fou : si le LLM termine SANS message narratif final
                # (chunk_text vide à ce dernier tour), forcer un récap court
                # pour ne JAMAIS laisser l'user devant un turn qui se termine
                # juste après un résultat tool brut sans explication.
                # Cas typique : agent fait 2 tools puis "stop" sans expliquer.
                if not chunk_text.strip():
                    if tool_calls_made:
                        # Au moins 1 tool a tourné — forcer un récap LLM rapide
                        messages.append({
                            "role": "system",
                            "content": (
                                "Conclus MAINTENANT le tour avec un message "
                                "non-technique à l'user : (1) ce qui a été "
                                "fait, (2) ce qui reste, (3) propose la suite. "
                                "PAS de tool call, JUSTE un message."
                            ),
                        })
                        try:
                            async with httpx.AsyncClient(timeout=60) as client_f:
                                async with client_f.stream(
                                    "POST",
                                    f"{_LLM_BASE_URL}/chat/completions",
                                    json={
                                        "model": model,
                                        "messages": messages,
                                        "stream": True,
                                        "max_tokens": 600,
                                    },
                                    headers={
                                        "Authorization": f"Bearer {_LLM_API_KEY}",
                                        "Content-Type":  "application/json",
                                    }
                                ) as resp_f:
                                    async for line in resp_f.aiter_lines():
                                        if not line.startswith("data: "):
                                            continue
                                        data = line[6:]
                                        if data == "[DONE]":
                                            break
                                        try:
                                            d = json.loads(data)
                                            ct = d["choices"][0].get("delta", {}).get("content") or ""
                                            if ct:
                                                full_response += ct
                                                yield ct
                                        except Exception:
                                            pass
                        except Exception:
                            fb = (
                                "\n\n_J'ai exécuté quelques actions mais je n'ai "
                                "pas pu finaliser proprement ce tour. Veux-tu "
                                "que je continue sur la suite ou que je résume "
                                "ce qui a été fait ?_"
                            )
                            full_response += fb
                            yield fb
                    elif not full_response.strip():
                        # Aucun tool ni message — bulle vide totale
                        fb = (
                            "_Je n'ai pas généré de réponse. Reformule ta "
                            "demande et je m'en occupe._"
                        )
                        full_response += fb
                        yield fb
                break
            # Tool calls présents → les exécuter et boucler pour la suite
            tool_calls = list(tool_call_data.values())
            messages.append({
                "role":       "assistant",
                "content":    chunk_text or None,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"] or "{}")
                except Exception:
                    fn_args = {}

                log.info("Tool call: %s(%s)", fn_name, str(fn_args)[:100])
                yield f"\n\n> **`{fn_name}`**"
                if fn_args:
                    def _short_arg(v):
                        s = str(v).replace("\n", " ").replace("\r", " ").replace("`", "'")
                        s = " ".join(s.split())
                        return s[:40] + ("…" if len(s) > 40 else "")
                    args_preview = ", ".join(
                        f"`{k}={_short_arg(v)}`" for k, v in fn_args.items()
                    )
                    yield f" — {args_preview}"
                yield "\n"

                result = await _call_mcp_tool(fn_name, fn_args, username=self.username)
                tool_calls_made.append({"tool": fn_name, "args": fn_args, "result": result[:200]})

                result_clean = result.strip()
                if result_clean and result_clean not in ("{}", "null", "[]"):
                    if "![" in result_clean and "](data:" in result_clean:
                        yield f"\n{result_clean}\n"
                    else:
                        result_preview = result_clean[:300] + (
                            "..." if len(result_clean) > 300 else ""
                        )
                        yield f"\n```\n{result_preview}\n```\n"

                llm_result = re.sub(
                    r'!\[[^\]]*\]\(data:image/[^)]+\)',
                    '[image affichée à l\'utilisateur]',
                    result,
                )
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc["id"],
                    "content":      llm_result,
                })

                err_sig = _extract_error_signature(llm_result)
                if err_sig:
                    same_failures = [
                        c for c in tool_calls_made
                        if c["tool"] == fn_name
                        and _extract_error_signature(c.get("result", "")) == err_sig
                    ]
                    if len(same_failures) >= 2:
                        log.warning(
                            "Boucle d'erreur détectée : %s × %d sur %s",
                            err_sig, len(same_failures), fn_name,
                        )
                        messages.append({
                            "role": "system",
                            "content": (
                                f"⚠️ Tu as rencontré {len(same_failures)} fois "
                                f"la même erreur avec `{fn_name}` : « {err_sig} ». "
                                "ARRÊTE de retenter la même approche. Soit tu "
                                "changes complètement de méthode (autre algorithme, "
                                "autre param format, code Python brut au lieu de "
                                "processing.run, etc.), soit tu termines en "
                                "expliquant à l'utilisateur ce qui bloque et "
                                "ce que tu lui suggères. NE BOUCLE PAS."
                            ),
                        })
        else:
            # max_iterations atteint sans break : forcer un dernier appel LLM
            # de conclusion pour ne JAMAIS laisser l'user devant une conv figée.
            messages.append({
                "role": "system",
                "content": (
                    "Tu as atteint la limite d'étapes pour ce tour. CONCLUS "
                    "maintenant avec un message NON-TECHNIQUE pour l'utilisateur "
                    "expliquant : (1) ce qui a été réalisé concrètement, (2) ce "
                    "qui reste à faire pour atteindre son objectif initial, (3) "
                    "une question simple pour qu'il décide de la suite. PAS de "
                    "tool calls. Juste un message en français clair, sans jargon "
                    "Python, sans mention d'« itérations » ou de tools."
                ),
            })
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    async with client.stream(
                        "POST",
                        f"{_LLM_BASE_URL}/chat/completions",
                        json={
                            "model": model,
                            "messages": messages,
                            "stream": True,
                            "max_tokens": 1024,
                        },
                        headers={
                            "Authorization": f"Bearer {_LLM_API_KEY}",
                            "Content-Type":  "application/json",
                        }
                    ) as resp_final:
                        async for line in resp_final.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                d = json.loads(data)
                                delta = d["choices"][0].get("delta", {})
                                content = delta.get("content") or ""
                                if content:
                                    full_response += content
                                    yield content
                            except Exception:
                                pass
            except Exception:
                fallback = (
                    "\n\n_Je n'ai pas pu terminer entièrement le travail dans "
                    "ce tour. Veux-tu que je continue sur la suite, ou que je "
                    "résume ce qui a été fait jusqu'ici ?_"
                )
                full_response += fallback
                yield fallback

        # Détecter directives <remember> en fin de turn → insights mémoire
        import re as _re_end
        remember_matches = _re_end.findall(
            r'<remember>\s*([^<:]+)\s*:\s*([^<]+?)\s*</remember>',
            full_response, flags=_re_end.IGNORECASE,
        )
        if remember_matches:
            for key, value in remember_matches:
                key_clean = key.strip()[:80]
                value_clean = value.strip()[:300]
                if key_clean and value_clean:
                    try:
                        await memory.add_insight(
                            key=key_clean, value=value_clean,
                            source="implicit", confidence=0.8,
                            username=self.username,
                        )
                        log.info("Insight enregistré: %s = %s", key_clean, value_clean)
                    except Exception as exc:
                        log.warning("Add insight err: %s", exc)
            # Signal discret
            yield f"\n\n*🧠 Mémorisé : {len(remember_matches)} insight(s).*"
            # Nettoyer du full_response avant save
            full_response = _re_end.sub(
                r'<remember>[^<]*</remember>\s*',
                '', full_response, flags=_re_end.IGNORECASE,
            )

        # Détecter directive de switch profil dans la réponse complète
        m = _re_end.search(
            r'<switch_profile>\s*([a-zA-Z0-9_]+)\s*</switch_profile>',
            full_response, flags=_re_end.IGNORECASE,
        )
        if m:
            new_profile = m.group(1).strip().lower()
            log.info("LLM directive switch_profile → %s", new_profile)
            await self.set_profile(new_profile)
            # Persister côté hub : étude active de l'user prend ce profil
            try:
                if _HUB_URL and _HUB_KEY:
                    async with httpx.AsyncClient(timeout=10) as c:
                        active = await c.get(
                            f"{_HUB_URL}/studies/active",
                            headers={"Authorization": f"Bearer {_HUB_KEY}"},
                        )
                        if active.status_code == 200 and active.json():
                            sid = active.json()["id"]
                            await c.patch(
                                f"{_HUB_URL}/studies/{sid}",
                                headers={"Authorization": f"Bearer {_HUB_KEY}"},
                                json={"profile": new_profile},
                            )
            except Exception as exc:
                log.warning("Persist switch profile : %s", exc)
            # Signal discret au front (markdown italic)
            yield f"\n\n*Profil basculé sur **{new_profile}**.*"
            # Nettoyer la directive du full_response sauvegardé
            full_response = _re_end.sub(
                r'<switch_profile>\s*[a-zA-Z0-9_]+\s*</switch_profile>\s*',
                '', full_response, flags=_re_end.IGNORECASE,
            )

        # Sauvegarder la réponse complète en mémoire. Les data-URLs d'images
        # sont stripées de l'historique persisté : sinon, au turn N+1, le
        # contexte LLM enfle de ~300 KB par screenshot et casse le modèle.
        # L'utilisateur ne perd rien — l'image est dans la bulle DOM courante.
        full_response_safe = re.sub(
            r'!\[[^\]]*\]\(data:image/[^)]+\)',
            '[image affichée précédemment à l\'utilisateur]',
            full_response,
        )
        await memory.add_message(
            self.session_id, "assistant", full_response_safe,
            tool_calls=tool_calls_made if tool_calls_made else None
        )

        # Auto-extraction d'insights : tous les 6 messages user dans la session.
        # Fire-and-forget — n'impacte pas la réponse en cours. Le LLM appelle
        # est rate-limité naturellement par le seuil + l'idempotence d'add_insight.
        try:
            msgs = await memory.get_session_messages(self.session_id, limit=200)
            user_count = sum(1 for m in msgs if m.get("role") == "user")
            if user_count >= 3 and user_count % 6 == 0:
                from agent import insight_extractor
                asyncio.create_task(
                    insight_extractor.extract_for_session(
                        self.session_id, self.username or "user",
                    )
                )
        except Exception as e:
            log.debug("auto insight_extractor skip: %s", e)

        # Auto-save du projet QGIS si ce turn a modifié l'état (tool whitelist).
        # Fire-and-forget vers le hub : exec save_project côté workspace. Garantit
        # que /data/studies/{active}/project.qgz suit le travail courant sans que
        # l'agent ait à appeler save_project explicitement.
        if any(c["tool"] in _MUTATING_TOOLS for c in tool_calls_made):
            asyncio.create_task(self._autosave_active_study())
