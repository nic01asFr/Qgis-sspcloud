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

async def study_list_handler(
    username: str, args: dict, mcp_session_id: str | None = None,
) -> dict:
    """Retourne la liste des etudes actives du user + celle active.

    Day 3 : active_sid resolu via priorite session_state > A1 > DB user.
    Garantit que l'etude active (session-scoped ou DB) est TOUJOURS dans
    la liste retournee, meme si status != active (fix edge case study_list).

    Ajoute champs :
    - `is_session_active` : True si l'entree correspond a l'active_sid
      session-scoped (distinct de is_active DB)
    - `status` : expose le status brut pour debug
    - `session_scoped` (root) : True si la session courante a un scope MCP
    """
    from hub import studies
    from hub.main import resolve_effective_active_sid
    all_studies = await studies.list_studies(username)
    # Day 3 : sid effectif via priorite
    effective_sid, _ = await resolve_effective_active_sid(
        username, mcp_session_id, None,
    )
    # Filtre status=active ; puis re-inject l'etude active si absente
    filtered = [s for s in (all_studies or []) if s.get("status") == "active"]
    if effective_sid and not any(s["id"] == effective_sid for s in filtered):
        # Chercher l'etude active dans all_studies (peut etre archived)
        active_s = next(
            (s for s in (all_studies or []) if s["id"] == effective_sid),
            None,
        )
        if active_s:
            filtered.append(active_s)
    return {
        "studies": [
            {
                "sid": s["id"],
                "name": s["name"],
                "profile": s.get("profile", "standard"),
                "last_active": s.get("last_active"),
                "status": s.get("status", "active"),
                "is_active": s["id"] == effective_sid,
            }
            for s in filtered
        ],
        "active_sid": effective_sid,
        "session_scoped": bool(mcp_session_id) and effective_sid is not None,
    }


async def study_create_handler(
    username: str, args: dict, execute_python_in_workspace_fn,
    mcp_session_id: str | None = None,
) -> dict:
    """Cree etude + default project + active. Reutilise studies.create_study
    + chained default project (Sprint UX-3 Commit 2 pattern).

    Day 3 : si mcp_session_id fourni, l'activation est session-scoped
    (ecrit dans session_active_state) au lieu de DB user. Le pod QGIS est
    active pour rendre le contexte immediatement utilisable par les tools
    suivants dans la meme session MCP.
    """
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
    # Day 3 : activation session-scoped ou globale selon presence mcp_session_id
    if mcp_session_id:
        from hub import session_active_state as _sas
        await _sas.set_active(mcp_session_id, sid, default_pid, username=username)
    else:
        # Legacy Day 2 : mute DB user (comportement historique preserve)
        await studies.set_active_study(username, sid)
    await studies.touch_study(sid)
    if default_pid:
        if not mcp_session_id:
            await studies.set_active_project(username, default_pid)
        await studies.touch_project(default_pid)
    # Switch physique du pod QGIS (necessaire dans les 2 cas pour que les
    # tools workspace suivants operent sur le bon .qgz)
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
        "session_scoped": bool(mcp_session_id),
    }


async def study_switch_handler(
    username: str, args: dict, execute_python_in_workspace_fn,
    mcp_session_id: str | None = None,
) -> dict:
    """Bascule etude active + save prev (dual-write pid-scope) + chained default.

    Day 3 : si mcp_session_id fourni, la nouvelle etude est session-scoped
    (ecrit dans session_active_state, NE MUTE PAS DB user). Le save sortante
    utilise l'etat effectif AVANT switch (donc soit session_state precedent
    soit DB user selon la source). Le switch physique du pod QGIS est fait
    dans les 2 cas.
    """
    from hub import studies
    sid = args.get("sid")
    if not sid:
        raise ValueError("Argument 'sid' obligatoire")
    s = await studies.get_study(sid, username)
    if not s:
        raise ValueError(f"Etude {sid} introuvable")
    if s.get("status") != "active":
        raise ValueError(f"Etude {sid} archivee (status={s.get('status')})")

    # Day 3 : etat SORTANT depend de la scope courante
    if mcp_session_id:
        from hub import session_active_state as _sas
        prev_sid, prev_pid = await _sas.get_active(mcp_session_id)
        # Si session_state vide, fallback DB pour save sortante propre
        if not prev_sid:
            prev_sid = await studies.get_active_study_id(username)
            prev_pid = await studies.get_active_project_id(username)
    else:
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

    # Ecriture active : session-scoped ou DB user selon mcp_session_id
    default_p = await studies.get_default_project(sid)
    default_pid = default_p["pid"] if default_p else None
    if mcp_session_id:
        from hub import session_active_state as _sas
        await _sas.set_active(mcp_session_id, sid, default_pid, username=username)
    else:
        await studies.set_active_study(username, sid)
        if default_pid:
            await studies.set_active_project(username, default_pid)
    await studies.touch_study(sid)
    if default_pid:
        await studies.touch_project(default_pid)

    # Switch physique pod QGIS (dans les 2 cas)
    try:
        await execute_python_in_workspace_fn(
            username, studies.activate_pod_code(sid),
        )
        if default_pid:
            try:
                await execute_python_in_workspace_fn(
                    username, studies.activate_project_pod_code(sid, default_pid),
                )
            except Exception as exc:
                log.warning("study_switch: activate projet %s : %s",
                            default_pid, exc)
    except Exception as exc:
        log.warning("study_switch: activate pod %s : %s", sid, exc)

    return {
        "active_sid": sid,
        "active_pid": default_pid,
        "name": s["name"],
        "session_scoped": bool(mcp_session_id),
    }


async def study_project_list_handler(
    username: str, args: dict, mcp_session_id: str | None = None,
) -> dict:
    """Liste les projets d'une etude (defaut = active).

    Day 3 : sid effectif via priorite session > A1 > DB. active_pid retourne
    depend de la scope courante (session_state ou DB user).
    """
    from hub import studies
    from hub.main import resolve_effective_active_sid
    sid = args.get("sid")
    effective_pid = None
    if not sid:
        # Day 3 : resolve prioritise session_state
        sid, effective_pid = await resolve_effective_active_sid(
            username, mcp_session_id, None,
        )
    else:
        # sid explicite -> active_pid depend de la scope de la session
        if mcp_session_id:
            from hub import session_active_state as _sas
            cur_sid, cur_pid = await _sas.get_active(mcp_session_id)
            # Ne montre le pid session-scope que s'il correspond au sid demande
            if cur_sid == sid:
                effective_pid = cur_pid
        if effective_pid is None:
            effective_pid = await studies.get_active_project_id(username)
    if not sid:
        raise ValueError(
            "Aucune etude active - passer 'sid' explicite ou utiliser "
            "study_switch d'abord"
        )
    projects = await studies.list_projects(sid)
    return {
        "sid": sid,
        "projects": [
            {
                "pid": p["pid"],
                "label": p["label"],
                "is_default": bool(p.get("is_default")),
                "is_active": p["pid"] == effective_pid,
                "last_active": p.get("last_active"),
            }
            for p in (projects or [])
            if p.get("status") == "active"
        ],
        "active_pid": effective_pid,
        "session_scoped": bool(mcp_session_id) and effective_pid is not None,
    }


async def study_project_create_handler(
    username: str, args: dict, execute_python_in_workspace_fn,
    mcp_session_id: str | None = None,
) -> dict:
    """Cree un nouveau projet dans une etude + l'active.

    Day 3 : sid resolu via priorite session_state > DB user. Activation
    du nouveau projet session-scoped si mcp_session_id present.
    """
    from hub import studies
    label = (args.get("label") or "").strip()
    if not label:
        raise ValueError("Le label du projet est obligatoire")
    sid = args.get("sid")
    prev_pid = None
    if not sid:
        # Day 3 : sid effectif via priorite session > DB
        if mcp_session_id:
            from hub import session_active_state as _sas
            sid, prev_pid = await _sas.get_active(mcp_session_id)
        if not sid:
            sid = await studies.get_active_study_id(username)
            prev_pid = await studies.get_active_project_id(username)
    else:
        # sid explicite -> prev_pid lu du meme scope que celui qui sera muta
        if mcp_session_id:
            from hub import session_active_state as _sas
            _cur_sid, prev_pid = await _sas.get_active(mcp_session_id)
            # Si la session pointait ailleurs, on quand meme utilise
            # prev_pid du meme scope pour save (peut etre None -> ok)
        else:
            prev_pid = await studies.get_active_project_id(username)
    if not sid:
        raise ValueError("Aucune etude active - passer 'sid' explicite")
    s = await studies.get_study(sid, username)
    if not s:
        raise ValueError(f"Etude {sid} introuvable")

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

    # Day 3 : activate session-scoped ou DB user
    if mcp_session_id:
        from hub import session_active_state as _sas
        await _sas.set_active(mcp_session_id, sid, new_pid, username=username)
    else:
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
        "session_scoped": bool(mcp_session_id),
    }


async def study_project_switch_handler(
    username: str, args: dict, execute_python_in_workspace_fn,
    mcp_session_id: str | None = None,
) -> dict:
    """Bascule sur un autre projet + save prev (dual-write intra-etude fix #2).

    Day 3 : etat SORTANT depend de la scope courante (session ou DB). Ecriture
    active session-scoped si mcp_session_id present. Cascade etude cross-etude
    idem session-scoped.
    """
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
    # Day 3 : lecture etat sortant selon scope courante
    if mcp_session_id:
        from hub import session_active_state as _sas
        prev_sid, prev_pid = await _sas.get_active(mcp_session_id)
        if not prev_sid:
            prev_sid = await studies.get_active_study_id(username)
            prev_pid = await studies.get_active_project_id(username)
    else:
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
                log.warning("study_project_switch: save sortante etude %s : %s",
                            prev_sid, exc)
        # Day 3 : activate etude session-scoped ou DB
        if not mcp_session_id:
            await studies.set_active_study(username, target_sid)
        await studies.touch_study(target_sid)
        try:
            await execute_python_in_workspace_fn(
                username, studies.activate_pod_code(target_sid),
            )
        except Exception as exc:
            log.warning("study_project_switch: activate etude %s : %s",
                        target_sid, exc)
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

    # Day 3 : activate projet session-scoped ou DB
    if mcp_session_id:
        from hub import session_active_state as _sas
        await _sas.set_active(mcp_session_id, target_sid, pid, username=username)
    else:
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
        "session_scoped": bool(mcp_session_id),
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
    mcp_session_id: str | None = None,
) -> dict:
    """Dispatche un tools/call vers le bon handler local.

    Args:
        tool_name: nom du tool (ex: "study_create")
        args: arguments MCP tools/call
        username: user courant (pour scoping DB)
        execute_python_in_workspace_fn: reference vers hub.main._execute_python_in_workspace
                                        pour les handlers qui doivent activer/save cote pod
        mcp_session_id: Day 3 - identifiant session MCP (Mcp-Session-Id header)
                        pour scoping session-level de l'active_sid/pid. Si None,
                        comportement Day 2 (mute DB user).

    Returns:
        dict serialisable JSON (contenu de result.content[0].text apres wrap MCP)

    Raises:
        ValueError si tool_name inconnu ou args invalides
    """
    if tool_name not in HUB_TOOL_HANDLERS:
        raise ValueError(f"Hub tool inconnu : {tool_name}")
    handler, needs_execute = HUB_TOOL_HANDLERS[tool_name]
    if needs_execute:
        return await handler(
            username, args, execute_python_in_workspace_fn,
            mcp_session_id=mcp_session_id,
        )
    return await handler(username, args, mcp_session_id=mcp_session_id)
