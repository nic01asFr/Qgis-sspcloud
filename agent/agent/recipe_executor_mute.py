"""agent.recipe_executor_mute — Exécution mute d'une recipe web (Chantier G8).

Court-circuite le LLM freeform lorsqu'une session porte un
``context_kind=recipe_run`` (session_id parsé via ``memory.parse_session_id``).
Le pipeline est déterministe strict : appel du hub ``/api/recipes-web/execute``
qui exécute ``execute_recipe_pure`` (moteur G4-POC), puis narratif final
généré par template (pas de LLM).

Points d'attention :
  - Aucun appel LLM dans ce module (function_call, generate, completion :
    tous interdits). C'est la propriété qui donne son sens à "mute".
  - Le narratif final est un template déterministe simple : ``PAS DE LLM``.
  - Fail-soft : si le hub renvoie 404/400/500, on émet un event ``recipe_error``
    et on stoppe le stream proprement (pas de crash côté frontend).
  - Le tag session ``execution_mode=recipe_pure`` est set en toute fin (post
    ``recipe_done``), best effort (une erreur SQLite ne fait pas planter).

API :
  - ``async stream_recipe_execution(session_id, recipe_id, hub_url, api_key,
    user_message) -> AsyncIterator[str]`` : yield d'events SSE.

Format SSE émis (dans l'ordre) :
  - ``event: recipe_start\\ndata: {recipe_id, session_id, mode}``
  - ``event: recipe_step\\ndata: {step_index, step_id, status}`` (0..N)
  - ``event: recipe_output\\ndata: {scene_manifest_summary}``
  - ``event: recipe_done\\ndata: {output, publish_hint, narrative}``

Sur erreur (recipe absente, YAML invalide, engine KO) :
  - ``event: recipe_error\\ndata: {message, http_status?, recipe_id}``
  - puis fin du stream (pas de recipe_done).
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from agent import memory

log = logging.getLogger("agent.recipe_executor_mute")


# ── Format SSE ───────────────────────────────────────────────────────────────


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Sérialise un event SSE conforme (event + data + double newline).

    Format identique à celui déjà utilisé dans /chat pour le stream texte,
    mais avec un ``event:`` explicite (le frontend écoute par event name).
    """
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


# ── Narratif final (template déterministe, PAS de LLM) ───────────────────────


def _build_final_narrative(
    recipe_id: str, output: dict[str, Any], user_message: str | None,
) -> str:
    """Compose un résumé humain-friendly de l'exécution.

    Template pur — jamais d'appel LLM. Le narratif reste stable pour la même
    entrée, ce qui garantit la reproductibilité côté frontend.
    """
    scene = output.get("scene_manifest") or {}
    title = scene.get("title") or recipe_id
    layers = scene.get("layers") or []
    # Un assembly emballe le composant dans `components[0].inline` — remonter
    # le count layers depuis là si présent (POC G4).
    if not layers:
        for comp in scene.get("components", []) or []:
            inline = comp.get("inline") or {}
            layers = inline.get("layers") or []
            if layers:
                break
    briques = output.get("briques_used") or []
    provenance = output.get("provenance") or {}
    output_kind = provenance.get("output_kind") or "component"

    parts = [
        f"Recipe « {title} » exécutée en mode déterministe (recipe_pure).",
        (
            f"Sortie : {output_kind} avec {len(layers)} couche(s), "
            f"{len(briques)} brique(s) appliquée(s)."
        ),
    ]
    if briques:
        listed = ", ".join(briques[:5])
        if len(briques) > 5:
            listed += f", … (+{len(briques) - 5})"
        parts.append(f"Briques : {listed}.")
    parts.append("Publier le manifest ?")
    return " ".join(parts)


def _summarize_scene_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Retourne un résumé léger du scene_manifest pour l'event ``recipe_output``.

    On évite d'inclure le manifest entier dans un event intermédiaire : il est
    déjà transporté dans ``recipe_done``. Ici on ne garde que titre + counts.
    """
    layers = manifest.get("layers") or []
    if not layers:
        for comp in manifest.get("components", []) or []:
            inline = comp.get("inline") or {}
            layers = inline.get("layers") or []
            if layers:
                break
    return {
        "title": manifest.get("title"),
        "manifest_version": manifest.get("manifest_version"),
        "produced_at": manifest.get("produced_at"),
        "n_layers": len(layers),
        "output_kind": (manifest.get("provenance") or {}).get("output_kind"),
    }


# ── Appel HTTP hub ───────────────────────────────────────────────────────────


async def _call_hub_execute(
    hub_url: str,
    api_key: str,
    recipe_id: str,
    user_message: str | None,
) -> tuple[int, dict[str, Any] | str]:
    """Appelle POST {hub_url}/api/recipes-web/execute avec Bearer api_key.

    Retourne ``(status_code, body)`` où body est le JSON parsé si possible,
    sinon la chaîne brute. Ne lève pas — le stream gère l'erreur en amont.
    """
    url = f"{hub_url.rstrip('/')}/api/recipes-web/execute"
    payload = {
        "recipe_id": recipe_id,
        "context": {"user_message": user_message or ""},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Hub-Auth": api_key,  # symétrie avec le pattern agent→hub existant.
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            return 599, f"httpx error: {exc}"
        try:
            body: dict[str, Any] | str = resp.json()
        except Exception:
            body = resp.text
        return resp.status_code, body


# ── Stream principal ─────────────────────────────────────────────────────────


async def stream_recipe_execution(
    session_id: str,
    recipe_id: str,
    hub_url: str,
    api_key: str,
    user_message: str | None,
) -> AsyncIterator[str]:
    """Streame l'exécution mute d'une recipe web côté frontend.

    Contract :
      1. yield ``recipe_start``
      2. appel hub /api/recipes-web/execute (bloquant côté agent)
      3. yield ``recipe_step`` pour chaque step listé dans provenance.steps_order
      4. yield ``recipe_output`` (résumé)
      5. yield ``recipe_done`` (output complet + narrative + publish_hint)
      6. set tag session ``execution_mode=recipe_pure``

    Sur erreur hub (status != 200) : yield ``recipe_error`` et retour.
    """
    log.info(
        "stream_recipe_execution session=%s recipe=%s",
        session_id[:16], recipe_id,
    )

    yield _sse(
        "recipe_start",
        {
            "recipe_id": recipe_id,
            "session_id": session_id,
            "mode": "recipe_pure",
        },
    )

    status, body = await _call_hub_execute(
        hub_url, api_key, recipe_id, user_message,
    )

    if status != 200:
        detail = (
            body.get("detail")
            if isinstance(body, dict) and "detail" in body
            else str(body)
        )
        yield _sse(
            "recipe_error",
            {
                "message": detail,
                "http_status": status,
                "recipe_id": recipe_id,
            },
        )
        # Telemetry best-effort : marquer la session échec.
        try:
            await memory.set_session_tag(
                session_id, "recipe_run_failed", recipe_id,
            )
        except Exception as exc:  # pragma: no cover
            log.debug("set_session_tag recipe_run_failed : %s", exc)
        return

    # Success : body est un RecipeWebOutput.model_dump().
    if not isinstance(body, dict):
        # Réponse 200 non-dict : anomalie, on la remonte comme erreur.
        yield _sse(
            "recipe_error",
            {
                "message": "Réponse hub inattendue (non-dict)",
                "http_status": status,
                "recipe_id": recipe_id,
            },
        )
        return

    output: dict[str, Any] = body
    scene = output.get("scene_manifest") or {}
    provenance = output.get("provenance") or {}
    steps_order = provenance.get("steps_order") or []

    # 3. Progression steps (rétrospective — l'engine ne stream pas).
    for idx, step_id in enumerate(steps_order):
        yield _sse(
            "recipe_step",
            {
                "step_index": idx,
                "step_id": step_id,
                "status": "done",
            },
        )

    # 4. Résumé scene_manifest.
    yield _sse(
        "recipe_output",
        {"scene_manifest_summary": _summarize_scene_manifest(scene)},
    )

    # 5. Narratif final + publish_hint (déterministe, pas de LLM).
    narrative = _build_final_narrative(recipe_id, output, user_message)
    publish_hint = {
        "endpoint": "/publish/scene_manifest",
        "recipe_id": recipe_id,
        "produced_at": scene.get("produced_at"),
    }
    yield _sse(
        "recipe_done",
        {
            "output": output,
            "publish_hint": publish_hint,
            "narrative": narrative,
        },
    )

    # 6. Telemetry — best effort.
    try:
        await memory.set_session_tag(
            session_id, "execution_mode", "recipe_pure",
        )
        await memory.set_session_tag(
            session_id, "recipe_run_ok", recipe_id,
        )
    except Exception as exc:  # pragma: no cover
        log.debug("set_session_tag execution_mode : %s", exc)
