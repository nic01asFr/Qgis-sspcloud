"""hub.mcp_hub_tools — 6 tools MCP hub natifs pour gestion des etudes/projets.

Sprint isolation etudes-projets Fix #4 (2026-07-30).

## Contexte

Le proxy MCP hub (main.py:mcp_auto_session) forwarde actuellement tout
`tools/list` et `tools/call` vers le pod workspace QGIS. Consequence : les
clients MCP externes (Claude Desktop, Cursor, Cline) voient uniquement les
46 tools workspace (add_layer, execute_python, export_pdf, upload_file...)
qui manipulent le fichier .qgz en RAM du process QGIS Desktop.

Aucun tool n'existait pour gerer la couche DB "etudes CEREMA" (creer une
nouvelle etude, lister ses projets, basculer entre projets DB). Le seul
chemin passait par l'UI /desk. Consequence : quand un user configurait
Claude Desktop avec une entry `qgis-nicolaslaval`, toutes ses conversations
tapaient sur `active_study` du user sans qu'il puisse dispatcher.

## Solution

Le hub expose ses propres tools MCP dans `tools/list` en plus des tools
workspace. Le proxy `_proxy_request` :
- Sur `tools/list` : bufferise la reponse workspace + merge avec la liste
  hub-tools + renvoie l'union
- Sur `tools/call` : detecte si le tool name est `study_*` -> execute local
  contre l'API REST hub existante; sinon forward workspace comme avant

Les 6 tools respectent les nomenclatures :
- Prefixe `study_*` pour marquer clairement le namespace (evite confusion
  avec `new_project` / `open_project` / `save_project` workspace qui
  manipulent le fichier .qgz, pas la DB)
- Zero doublon avec les 46 tools workspace ni les 26+ native_tools_v2 agent
- Wrappers legers autour de `hub.studies` (single source of truth)

## Coexistence avec Sprint isolation A1/A2

Le mecanisme historique `X-Session-Id` header (patterns study:{sid},
assist:{sid}:..., agent:{aid}:sid:{sid}) reste supporte cote hub
(_ensure_active_study_for_agent). Mais aucun client MCP externe ne l'envoie
en pratique. Les nouveaux tools `study_switch` remplissent le meme role
en explicite : le LLM Claude Desktop les orchestre selon le prompt user.

Les 2 mecanismes tapent sur les MEMES fonctions studies.set_active_study +
activate_pod (idempotent).

## API

Chaque tool retourne un dict serialisable JSON. Erreurs remontees via
exception -> geree cote dispatcher main.py qui produit une reponse
JSON-RPC error propre pour le client MCP.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("hub.mcp_hub_tools")


# ── Schema JSON des 6 tools (spec MCP 2024-11-05) ────────────────────────────

HUB_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "study_list",
        "description": (
            "Liste les etudes CEREMA de l'utilisateur courant. Une etude est un "
            "workspace geospatial qui regroupe des projets QGIS, des donnees, "
            "des livrables et des conversations. Retourne les etudes actives "
            "avec leur statut d'activation (l'etude active est celle sur "
            "laquelle les autres tools QGIS operent par defaut)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "study_create",
        "description": (
            "Cree une nouvelle etude CEREMA + un projet 'principal' par defaut, "
            "et active immediatement cette etude. Apres appel, les tools QGIS "
            "suivants (add_layer, execute_python, export_pdf...) operent sur "
            "ce nouveau projet. Utiliser quand l'utilisateur demande de "
            "commencer un nouveau territoire ou une nouvelle analyse."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Nom lisible de l'etude (ex: 'PCRS Sorgues', 'Diagnostic "
                        "Marseille 4e'). Sera visible dans l'UI desk et les livrables."
                    ),
                    "minLength": 1,
                    "maxLength": 200,
                },
                "profile": {
                    "type": "string",
                    "description": (
                        "Profil d'etude optionnel (ex: 'standard', 'cadastre_solaire', "
                        "'diagnostic_pcrs'). Precharge des recipes et catalogues "
                        "specialises. Defaut : 'standard'."
                    ),
                    "default": "standard",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "study_switch",
        "description": (
            "Bascule sur une autre etude active. Sauve automatiquement l'etat "
            "de l'etude courante avant le switch (dual-write pid-scope + legacy) "
            "et charge le projet default de l'etude cible dans QGIS Desktop. "
            "Utiliser quand l'utilisateur veut reprendre le travail sur un autre "
            "territoire deja cree. IMPORTANT : appeler AVANT les tools QGIS pour "
            "s'assurer qu'ils operent sur la bonne etude."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sid": {
                    "type": "string",
                    "description": (
                        "Identifiant de l'etude cible (12 hex, obtenu via study_list)."
                    ),
                    "pattern": r"^[0-9a-f]{12}$",
                },
            },
            "required": ["sid"],
            "additionalProperties": False,
        },
    },
    {
        "name": "study_project_list",
        "description": (
            "Liste les projets QGIS d'une etude (chaque etude peut avoir plusieurs "
            "projets pour separer des analyses distinctes). Retourne les projets "
            "avec leur label, statut d'activation et derniere date d'edition. "
            "Sans argument, liste les projets de l'etude active."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sid": {
                    "type": "string",
                    "description": (
                        "Identifiant de l'etude cible (12 hex). Optionnel : "
                        "defaut = etude active."
                    ),
                    "pattern": r"^[0-9a-f]{12}$",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "study_project_create",
        "description": (
            "Cree un nouveau projet QGIS dans une etude + l'active immediatement. "
            "Utile quand l'utilisateur veut faire une analyse separee dans la "
            "meme etude (ex: comparaison scenarios). Sans sid, cree dans l'etude "
            "active. Le nouveau projet est un .qgz vide, pret a recevoir des layers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": (
                        "Nom lisible du projet (ex: 'Scenario A hausse', "
                        "'Comparaison RGA vs inondation')."
                    ),
                    "minLength": 1,
                    "maxLength": 200,
                },
                "sid": {
                    "type": "string",
                    "description": (
                        "Identifiant de l'etude parent (12 hex). Optionnel : "
                        "defaut = etude active."
                    ),
                    "pattern": r"^[0-9a-f]{12}$",
                },
            },
            "required": ["label"],
            "additionalProperties": False,
        },
    },
    {
        "name": "study_project_switch",
        "description": (
            "Bascule sur un autre projet QGIS de l'etude courante (ou d'une "
            "autre etude). Sauve automatiquement l'etat du projet courant "
            "(dual-write) et charge le projet cible dans QGIS Desktop. "
            "IMPORTANT : appeler AVANT les tools QGIS pour s'assurer qu'ils "
            "operent sur le bon projet. Si le projet est dans une autre etude, "
            "l'etude parent est aussi automatiquement activee."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "string",
                    "description": (
                        "Identifiant du projet cible (12 hex, obtenu via study_project_list)."
                    ),
                    "pattern": r"^[0-9a-f]{12}$",
                },
            },
            "required": ["pid"],
            "additionalProperties": False,
        },
    },
]


# ── Handlers (invoques par le dispatcher hub apres routing du tools/call) ────

async def study_list_handler(username: str, args: dict) -> dict:
    """Retourne la liste des etudes actives du user + celle active."""
    from hub import studies
    all_studies = await studies.list_studies(username)
    active_sid = await studies.get_active_study_id(username)
    return {
        "studies": [
            {
                "sid": s["id"],
                "name": s["name"],
                "profile": s.get("profile", "standard"),
                "last_active": s.get("last_active"),
                "is_active": s["id"] == active_sid,
            }
            for s in (all_studies or [])
            if s.get("status") == "active"
        ],
        "active_sid": active_sid,
    }


async def study_create_handler(
    username: str, args: dict, execute_python_in_workspace_fn,
) -> dict:
    """Cree etude + default project + active. Reutilise studies.create_study
    + chained default project (Sprint UX-3 Commit 2 pattern)."""
    from hub import studies
    name = (args.get("name") or "").strip()
    if not name:
        raise ValueError("Le nom de l'etude est obligatoire")
    profile = args.get("profile", "standard")
    s = await studies.create_study(
        username, name=name, profile=profile, origin="user",
    )
    sid = s["id"]
    # Init layout pod (mkdir + meta.json)
    try:
        await execute_python_in_workspace_fn(
            username, studies.init_pod_layout_code(sid, s["name"], s["profile"]),
        )
    except Exception as exc:
        log.warning("study_create: init layout %s : %s", sid, exc)
    # Chained default project (pattern activate_study endpoint)
    default_pid = None
    try:
        default_project = await studies.create_project(
            sid=sid, owner=username, label="Projet principal", is_default=True,
        )
        default_pid = default_project["pid"]
        await execute_python_in_workspace_fn(
            username,
            studies.create_project_pod_code(
                sid, default_pid, default_project["label"], copy_from=None,
            ),
        )
    except Exception as exc:
        log.warning("study_create: default project pour %s : %s", sid, exc)
    # Activate (pattern activate_study_endpoint)
    await studies.set_active_study(username, sid)
    await studies.touch_study(sid)
    if default_pid:
        await studies.set_active_project(username, default_pid)
        await studies.touch_project(default_pid)
    try:
        await execute_python_in_workspace_fn(
            username, studies.activate_pod_code(sid),
        )
        if default_pid:
            await execute_python_in_workspace_fn(
                username, studies.activate_project_pod_code(sid, default_pid),
            )
    except Exception as exc:
        log.warning("study_create: activate pod %s : %s", sid, exc)
    return {
        "sid": sid,
        "name": s["name"],
        "profile": s["profile"],
        "default_pid": default_pid,
        "is_active": True,
    }


async def study_switch_handler(
    username: str, args: dict, execute_python_in_workspace_fn,
) -> dict:
    """Bascule etude active + save prev (dual-write pid-scope) + chained default."""
    from hub import studies
    sid = args.get("sid")
    if not sid:
        raise ValueError("Argument 'sid' obligatoire")
    s = await studies.get_study(sid, username)
    if not s:
        raise ValueError(f"Etude {sid} introuvable")
    if s.get("status") != "active":
        raise ValueError(f"Etude {sid} archivee (status={s.get('status')})")

    prev_sid = await studies.get_active_study_id(username)
    prev_pid = await studies.get_active_project_id(username)
    if prev_sid and prev_sid != sid:
        # Save sortante avec dual-write pid-scope (Fix #1)
        try:
            await execute_python_in_workspace_fn(
                username,
                studies.save_active_project_pod_code(prev_sid, prev_pid),
            )
        except Exception as exc:
            log.warning("study_switch: save sortante %s : %s", prev_sid, exc)

    await studies.set_active_study(username, sid)
    await studies.touch_study(sid)
    try:
        await execute_python_in_workspace_fn(
            username, studies.activate_pod_code(sid),
        )
    except Exception as exc:
        log.warning("study_switch: activate pod %s : %s", sid, exc)

    # Chained default project (Sprint UX-3 Commit 2 pattern)
    default_p = await studies.get_default_project(sid)
    default_pid = None
    if default_p is not None:
        default_pid = default_p["pid"]
        await studies.set_active_project(username, default_pid)
        await studies.touch_project(default_pid)
        try:
            await execute_python_in_workspace_fn(
                username, studies.activate_project_pod_code(sid, default_pid),
            )
        except Exception as exc:
            log.warning("study_switch: activate projet %s : %s", default_pid, exc)
    return {
        "active_sid": sid,
        "active_pid": default_pid,
        "name": s["name"],
    }


async def study_project_list_handler(username: str, args: dict) -> dict:
    """Liste les projets d'une etude (defaut = active)."""
    from hub import studies
    sid = args.get("sid")
    if not sid:
        sid = await studies.get_active_study_id(username)
    if not sid:
        raise ValueError("Aucune etude active - passer 'sid' explicite ou utiliser study_switch d'abord")
    projects = await studies.list_projects(sid)
    active_pid = await studies.get_active_project_id(username)
    return {
        "sid": sid,
        "projects": [
            {
                "pid": p["pid"],
                "label": p["label"],
                "is_default": bool(p.get("is_default")),
                "is_active": p["pid"] == active_pid,
                "last_active": p.get("last_active"),
            }
            for p in (projects or [])
            if p.get("status") == "active"
        ],
        "active_pid": active_pid,
    }


async def study_project_create_handler(
    username: str, args: dict, execute_python_in_workspace_fn,
) -> dict:
    """Cree un nouveau projet dans une etude + l'active."""
    from hub import studies
    label = (args.get("label") or "").strip()
    if not label:
        raise ValueError("Le label du projet est obligatoire")
    sid = args.get("sid")
    if not sid:
        sid = await studies.get_active_study_id(username)
    if not sid:
        raise ValueError("Aucune etude active - passer 'sid' explicite")
    # Verifier acces
    s = await studies.get_study(sid, username)
    if not s:
        raise ValueError(f"Etude {sid} introuvable")

    prev_pid = await studies.get_active_project_id(username)
    # Save prev projet AVANT create (fix #2 pattern intra-etude)
    if prev_pid:
        try:
            await execute_python_in_workspace_fn(
                username,
                studies.save_active_project_pod_code(sid, prev_pid),
            )
        except Exception as exc:
            log.warning("study_project_create: save prev %s : %s", prev_pid, exc)

    new_project = await studies.create_project(
        sid=sid, owner=username, label=label, is_default=False,
    )
    new_pid = new_project["pid"]
    try:
        await execute_python_in_workspace_fn(
            username,
            studies.create_project_pod_code(sid, new_pid, label, copy_from=None),
        )
    except Exception as exc:
        log.warning("study_project_create: create pod %s : %s", new_pid, exc)

    # Activate
    await studies.set_active_project(username, new_pid)
    await studies.touch_project(new_pid)
    try:
        await execute_python_in_workspace_fn(
            username, studies.activate_project_pod_code(sid, new_pid),
        )
    except Exception as exc:
        log.warning("study_project_create: activate pod %s : %s", new_pid, exc)
    return {
        "pid": new_pid,
        "sid": sid,
        "label": label,
        "is_active": True,
    }


async def study_project_switch_handler(
    username: str, args: dict, execute_python_in_workspace_fn,
) -> dict:
    """Bascule sur un autre projet + save prev (dual-write intra-etude fix #2)."""
    from hub import studies
    pid = args.get("pid")
    if not pid:
        raise ValueError("Argument 'pid' obligatoire")
    p = await studies.get_project(pid, username)
    if not p:
        raise ValueError(f"Projet {pid} introuvable")
    if p.get("status") != "active":
        raise ValueError(f"Projet {pid} archive")

    target_sid = p["sid"]
    prev_sid = await studies.get_active_study_id(username)
    prev_pid = await studies.get_active_project_id(username)

    # Cascade activate etude si cross-etude
    if prev_sid != target_sid:
        # Save prev etude+pid AVANT switch (fix #1)
        if prev_sid and prev_pid:
            try:
                await execute_python_in_workspace_fn(
                    username,
                    studies.save_active_project_pod_code(prev_sid, prev_pid),
                )
            except Exception as exc:
                log.warning("study_project_switch: save sortante etude %s : %s", prev_sid, exc)
        await studies.set_active_study(username, target_sid)
        await studies.touch_study(target_sid)
        try:
            await execute_python_in_workspace_fn(
                username, studies.activate_pod_code(target_sid),
            )
        except Exception as exc:
            log.warning("study_project_switch: activate etude %s : %s", target_sid, exc)
    else:
        # Save intra-etude (fix #2) avant switch pid -> pid
        if prev_pid and prev_pid != pid:
            try:
                await execute_python_in_workspace_fn(
                    username,
                    studies.save_active_project_pod_code(target_sid, prev_pid),
                )
            except Exception as exc:
                log.warning("study_project_switch: save intra-etude %s/%s : %s",
                            target_sid, prev_pid, exc)

    await studies.set_active_project(username, pid)
    await studies.touch_project(pid)
    try:
        await execute_python_in_workspace_fn(
            username, studies.activate_project_pod_code(target_sid, pid),
        )
    except Exception as exc:
        log.warning("study_project_switch: activate projet %s : %s", pid, exc)
    return {
        "active_pid": pid,
        "active_sid": target_sid,
        "label": p["label"],
    }


# ── Registry : name -> (handler, requires_execute_python) ────────────────────

HUB_TOOL_HANDLERS = {
    "study_list": (study_list_handler, False),
    "study_create": (study_create_handler, True),
    "study_switch": (study_switch_handler, True),
    "study_project_list": (study_project_list_handler, False),
    "study_project_create": (study_project_create_handler, True),
    "study_project_switch": (study_project_switch_handler, True),
}


def is_hub_tool(tool_name: str) -> bool:
    """Retourne True si le tool_name est un hub-tool (pas workspace)."""
    return tool_name in HUB_TOOL_HANDLERS


async def dispatch_hub_tool(
    tool_name: str,
    args: dict,
    username: str,
    execute_python_in_workspace_fn,
) -> dict:
    """Dispatche un tools/call vers le bon handler local.

    Args:
        tool_name: nom du tool (ex: "study_create")
        args: arguments MCP tools/call
        username: user courant (pour scoping DB)
        execute_python_in_workspace_fn: reference vers hub.main._execute_python_in_workspace
                                        pour les handlers qui doivent activer/save cote pod

    Returns:
        dict serialisable JSON (contenu de result.content[0].text apres wrap MCP)

    Raises:
        ValueError si tool_name inconnu ou args invalides
    """
    if tool_name not in HUB_TOOL_HANDLERS:
        raise ValueError(f"Hub tool inconnu : {tool_name}")
    handler, needs_execute = HUB_TOOL_HANDLERS[tool_name]
    if needs_execute:
        return await handler(username, args, execute_python_in_workspace_fn)
    return await handler(username, args)
