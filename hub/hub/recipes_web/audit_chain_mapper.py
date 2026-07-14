"""hub.recipes_web.audit_chain_mapper -- Construit un AuditChain a partir
de la `provenance` d'un scene_manifest recipe_polished + optionnellement
des `treatments.jsonl` du workspace.

Sprint V0.4.2 Chantier C. Le principe 2 charte est INVIOLABLE :
tout chain-badge affiche dans un livrable = traitement reellement execute
et tracé dans treatments.jsonl. Ce mapper garantit que :

  - Les `recipes_used` reflethent la recipe reelle (slug + version).
  - Les `sources` viennent de `provenance.sources` (Strate-aligned).
  - Le `llm_provenance` (mode polished uniquement) trace le modele + tokens
    du polish narratif.
  - Les `tool_calls_made` proviennent de `treatments.jsonl` (source unique
    workspace) si fournis, sinon liste vide (mode stub / draft).

Volontairement conservateur : si un champ est manquant, on met une liste
vide plutot que d'inventer. Un audit incomplet est preferable a un audit
mensonger (Principe 2 §3 charte).
"""
from __future__ import annotations

import logging
from typing import Any

from hub.models.audit_chain import (
    AuditChain, LLMProvenance, Source,
)

log = logging.getLogger("hub.recipes_web.audit_chain_mapper")


def _extract_sources(provenance: dict[str, Any]) -> list[Source]:
    """Extract les sources du provenance en Source Pydantic Strate-strict.

    Fail-soft : sources malformees (champs manquants, types faux) sont
    loggees et skippees plutot que faire echouer le mapper.
    """
    raw = provenance.get("sources") or []
    if not isinstance(raw, list):
        return []
    sources: list[Source] = []
    for src in raw:
        if not isinstance(src, dict):
            continue
        try:
            # Source Strate exige `corpus` + `millesime` non-null.
            src_kwargs = {
                "corpus": str(src.get("corpus", "")),
                "ref_id": src.get("ref_id"),
                "millesime": str(src.get("millesime", "")),
                "authority": str(src.get("authority", "")),
                "licence": str(src.get("licence", "")),
                "url": src.get("url"),
                "statut": src.get("statut", "a_verifier"),
            }
            if not src_kwargs["corpus"] or not src_kwargs["millesime"]:
                # Source incomplete -- skip (mieux que faux positif).
                continue
            sources.append(Source(**src_kwargs))
        except Exception as exc:  # noqa: BLE001 -- fail-soft explicite
            log.debug(
                "audit_chain_mapper : source malformee skippee (%s)", exc,
            )
            continue
    return sources


def _extract_llm_provenance(provenance: dict[str, Any]) -> list[LLMProvenance]:
    """Extract la provenance LLM (mode polished uniquement).

    En mode `recipe_pure` : liste vide (aucun LLM implique -- deterministe).
    En mode `recipe_polished` : la provenance.polish contient les blocks
    polis. On agrege en 1 entree LLMProvenance globale par manifest.

    `prompt_hash` est calcule sur (POLISH_SYSTEM_PROMPT tag + model_id) --
    stable a modele + version prompt fixes, distinct si l'un change.
    """
    import hashlib

    polish = provenance.get("polish") or {}
    if not isinstance(polish, dict):
        return []
    polish_llm = polish.get("polish_llm_provenance") or {}
    if not isinstance(polish_llm, dict):
        return []
    model_name = polish_llm.get("model")
    if not model_name:
        return []
    try:
        # Marker prompt polish v1 (V0.4.1 polish_validator + prompt system
        # POLISH_SYSTEM_PROMPT). Cf. hub/recipes_web/polish.py.
        prompt_marker = f"polish_narrative_v1.0|{model_name}"
        prompt_hash = hashlib.sha256(
            prompt_marker.encode("utf-8")
        ).hexdigest()[:16]
        return [
            LLMProvenance(
                prompt_hash=prompt_hash,
                model_id=str(model_name),
                seed=None,
                temperature=None,
                tool_calls_count=int(polish_llm.get("blocks_polished", 0)),
            )
        ]
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "audit_chain_mapper : LLMProvenance skippee (%s)", exc,
        )
        return []


def _extract_recipes_used(provenance: dict[str, Any]) -> list[str]:
    """Extract les slugs de recettes utilisees.

    Une recipe_polished a une seule `recipe_used` en amont. Si l'assembly
    resulte d'une combinaison future (multi-recipes), la liste sera etendue.
    """
    recipe = provenance.get("recipe_used") or {}
    if isinstance(recipe, dict) and recipe.get("slug"):
        return [str(recipe["slug"])]
    return []


def _extract_tool_calls(
    treatments_lines: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Extract les tool calls de `treatments.jsonl`.

    Le format V0.1 workspace : chaque ligne = {ts, kind, tool, params,
    inputs, outputs, ok, ...}. On garde uniquement les entrees `ok=True`
    et sans PII (pas de body user brut, seulement metadata).
    """
    if not treatments_lines:
        return []
    tool_calls: list[dict[str, Any]] = []
    for line in treatments_lines:
        if not isinstance(line, dict):
            continue
        if line.get("ok") is False:
            continue
        # Garde uniquement les champs cles (evite l'exfiltration accidentelle
        # de body user via params).
        tool_calls.append({
            "tool": line.get("tool", ""),
            "kind": line.get("kind", ""),
            "ts": line.get("ts", ""),
            "duration_ms": line.get("duration_ms", 0),
            "n_features_out": line.get("n_features_out", 0),
        })
    return tool_calls


def build_audit_chain_from_provenance(
    aid: str,
    sid: str,
    owner: str,
    provenance: dict[str, Any],
    components_refs: list[str],
    treatments_lines: list[dict[str, Any]] | None = None,
    classification: str = "cerema_internal",
) -> AuditChain:
    """Construit un AuditChain valide pour un Assembly produit par recipe.

    Args:
        aid: Assembly.id (12 hex).
        sid: Etude.id (12 hex).
        owner: username CEREMA.
        provenance: dict `provenance` du scene_manifest recipes_web.
        components_refs: list des cid persistes par assembly_bridge.
        treatments_lines: optionnel, lignes JSONL du workspace treatments.jsonl
            (source unique de verite audit trail cote workspace, Principe 2).
        classification: audience cible ("cerema_internal" par defaut).

    Returns:
        AuditChain avec `integrity_hash` calcule via compute_integrity_hash().
    """
    audit = AuditChain(
        aid=aid,
        sid=sid,
        owner=owner,
        classification=classification,  # type: ignore[arg-type]
        scene_hashes=[],  # rempli par Sprint V0.5 quand scene_manifest persiste
        components_refs=list(components_refs),
        recipes_used=_extract_recipes_used(provenance),
        tool_calls_made=_extract_tool_calls(treatments_lines),
        llm_provenance=_extract_llm_provenance(provenance),
        sources=_extract_sources(provenance),
    )
    # Calcul du integrity_hash (SHA256 canonical) -- ancrage tamper-evident.
    audit.integrity_hash = audit.compute_integrity_hash()
    return audit
