"""hub.recipes_web.assembly_bridge -- Conversion scene_manifest -> Assembly.

Sprint V0.4.2 Chantier B. Le mode ``recipe_polished`` produit un
``scene_manifest`` V0.3.1. Pour publier ce livrable en aligne avec la voie
agent chat existante (uniformite Sprint V0.4.2), on convertit le manifest
en une paire ``(Assembly, [Component, ...])`` persistee dans les tables
``assemblies_index`` + ``components_index`` puis on emet un
``publish_hint = {sid, aid}`` que le frontend utilise pour appeler
``/api/livrable/publish`` -- exactement le meme pipeline que l'agent qui
appelle ``publish_assembly`` via tools MCP.

Design
------
- Chaque bloc ``narrative_text`` du scene_manifest -> un ``Component
  kind="narrative_text"``.
- Chaque ``interactive_map`` block -> un ``Component kind="interactive_map"``
  avec ``ComponentSource(scope="study", scene_manifest_url=...)``.
- L'Assembly wraps l'ensemble dans un ``layout.scroll_vertical`` (kind
  ``storymap_narrative_dsfr``, defaut le plus proche des scene_manifests
  recipe_polished actuels).
- ``footer`` par defaut : mentions legales CEREMA + sources du scene_manifest.
- ``audit_chain`` reste ``None`` a ce stade -- rempli au moment du publish
  par le mapper Chantier C (``audit_chain_mapper``).

Contract
--------
- ``sid`` est un 12-hex uuid4 -- doit venir du context recipe run
  (``parse_session_id`` cote agent extrait ``sid`` du session_id
  ``study:{sid}:recipe:{recipe_id}``). Le hub le recoit dans
  ``exec_context["sid"]``.
- Les rows ``assemblies_index`` et ``components_index`` sont INSERT-only
  (pattern V1.5 recipes_index) -- version_num=1 pour un premier run,
  N+1 si aid existe deja avec version=N (mais on genere un nouveau aid
  a chaque run polished, donc version=1 sauf republish force).

Idempotence
-----------
Un meme scene_manifest content_hash ne re-cree pas d'Assembly identique.
On lookup par ``content_hash`` avant d'inserer (todo Sprint V0.5 --
pour V0.4.2 on accepte le doublon si le user relance la meme recipe).
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from hub import assemblies, components
from hub.models.assembly import (
    Assembly, AssemblyFooter, AssemblyLayout, AssemblySection,
)
from hub.models.component import (
    Component, ComponentRendering, ComponentSource,
)
from hub.recipes_web.audit_chain_mapper import (
    build_audit_chain_from_provenance,
)


_SID_PATTERN = re.compile(r"^[0-9a-f]{12}$")

# Mapping kind scene_manifest.components[*].kind -> Component.kind + runtime.
# Le scene_manifest utilise ``narrative_text`` et ``interactive_map`` en
# priorite ; les autres kinds V0.3.1 (timeline, chart, legend, ...) sont
# capturables aussi mais tres rares en recipe polished actuel.
_KIND_TO_RUNTIME: dict[str, str] = {
    "narrative_text": "marked",
    "interactive_map": "maplibre",
    "scene_3d": "maplibre_three",
    "chart": "chartjs",
    "kpi_badge": "html",
    "kpi_grid": "html",
    "legend": "html",
    "heading": "html",
    "quote": "html",
    "separator": "html",
    "timeline": "html",
    "data_table": "datatables",
    "media_embed": "iframe",
}


def _new_id() -> str:
    """Genere un id 12-hex conforme au pattern Assembly/Component."""
    return uuid.uuid4().hex[:12]


def _build_component_source(
    block: dict[str, Any],
    sid: str,
    scene_manifest_url: str | None,
) -> ComponentSource | None:
    """Construit la source d'un Component a partir d'un bloc scene_manifest.

    Pour interactive_map / scene_3d / chart -> pointe vers le scene_manifest
    (source unique de verite). Pour narrative_text / kpi_badge / heading /
    quote / separator -> pas de source (kinds compositionnels).
    """
    kind = block.get("kind", "")
    if kind in ("narrative_text", "kpi_badge", "kpi_grid", "heading",
                "quote", "separator", "legend", "timeline"):
        return None
    return ComponentSource(
        scope="study",
        sid=sid,
        scene_hash=block.get("_scene_hash"),
        scene_manifest_url=scene_manifest_url,
    )


def _build_component_from_block(
    block: dict[str, Any],
    sid: str,
    scene_manifest_url: str | None,
) -> Component | None:
    """Convertit un bloc scene_manifest en Component Pydantic V0.1.

    Retourne None si le bloc n'a pas de kind exploitable (fail-soft :
    on skippe plutot que fail-hard, un manifest partiel est publiable).
    """
    kind = block.get("kind")
    if kind not in _KIND_TO_RUNTIME:
        return None

    title = block.get("title") or block.get("content", "")[:60] or f"{kind}"
    if len(title) > 300:
        title = title[:297] + "..."

    params: dict[str, Any] = {
        k: v for k, v in block.items()
        if k not in ("id", "kind", "title", "description")
    }

    return Component(
        id=_new_id(),
        version=1,
        kind=kind,
        title=title,
        description=block.get("description"),
        source=_build_component_source(block, sid, scene_manifest_url),
        params=params,
        rendering=ComponentRendering(
            runtime=_KIND_TO_RUNTIME[kind],
            container_size="responsive",
            theme="dsfr",
        ),
    )


def _extract_narrative_blocks(
    scene_manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract tous les blocs component-like du scene_manifest.

    Contract V0.3.1 : `components` (ou `narrative` en variante) est une
    liste de blocs typees. Layers (`layers`) sont un concept different
    -- consommes par les components interactive_map via `source`, pas
    materialises comme composants.
    """
    blocks: list[dict[str, Any]] = []
    for key in ("components", "narrative"):
        raw = scene_manifest.get(key)
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict) and entry.get("kind"):
                    blocks.append(entry)
    return blocks


def _default_footer(scene_manifest: dict[str, Any]) -> AssemblyFooter:
    """Construit le footer DSFR par defaut a partir du scene_manifest.

    Sources : extraites de ``provenance.sources`` ou de ``layers[].source``
    (format libre du manifest). Le disclaimer et les mentions CEREMA
    sont posees en defaut ; le user peut les editer avant publish.
    """
    sources_raw = (
        scene_manifest.get("provenance", {}).get("sources")
        or []
    )
    sources: list[dict[str, str]] = []
    for src in sources_raw:
        if isinstance(src, dict):
            sources.append({
                "corpus": str(src.get("corpus", "")),
                "ref_id": str(src.get("ref_id", "")),
                "millesime": str(src.get("millesime", "")),
                "authority": str(src.get("authority", "")),
                "licence": str(src.get("licence", "")),
                "url": str(src.get("url", "")),
            })
    return AssemblyFooter(
        sources=sources,
        cerema_mentions_legales=True,
    )


async def create_assembly_from_scene_manifest(
    scene_manifest: dict[str, Any],
    sid: str,
    owner: str,
    scene_manifest_url: str | None = None,
    title_override: str | None = None,
) -> tuple[str, list[str]]:
    """Convertit scene_manifest -> Assembly + Components et persiste en DB.

    Args:
        scene_manifest: manifest V0.3.1 produit par execute_recipe_polished.
        sid: etude.id 12-hex (extrait du session_id `study:{sid}:...`).
        owner: username CEREMA OIDC (`user["username"]` cote endpoint).
        scene_manifest_url: URL PVC du scene_manifest si persiste separement
            (optionnel -- Sprint V0.5 pour persister le manifest raw).
        title_override: force un titre custom (defaut = manifest["title"]).

    Returns:
        `(aid, [cid_1, cid_2, ...])` -- aid de l'Assembly cree, liste des
        cid persistes dans l'ordre d'apparition dans le manifest.

    Raises:
        ValueError: sid invalide (pattern ou empty).
        aiosqlite errors: en cas de conflit SQL, remonte.
    """
    if not sid or not _SID_PATTERN.match(sid):
        raise ValueError(
            f"sid invalide '{sid}' : attendu 12-hex uuid4 "
            "(pattern extrait de session_id 'study:{sid}:recipe:{...}')"
        )

    # 1. Extraire les blocs component-like du manifest et les convertir.
    blocks = _extract_narrative_blocks(scene_manifest)
    persisted_components: list[Component] = []
    persisted_cids: list[str] = []
    for block in blocks:
        component = _build_component_from_block(block, sid, scene_manifest_url)
        if component is None:
            continue
        await components.insert_component(
            component=component,
            owner=owner,
            sid=sid,
            file_path="",
            size_bytes=0,
        )
        persisted_components.append(component)
        persisted_cids.append(component.id)

    # 2. Construire l'Assembly wrappant les composants persistes.
    aid = _new_id()
    title = (
        title_override
        or scene_manifest.get("title")
        or f"Livrable recipe polished {aid[:8]}"
    )[:300]

    section = AssemblySection(
        kind="section",
        title=None,
        narrative_md=None,
        components=[{"ref": cid} for cid in persisted_cids],
    )
    # Sprint V0.4.2 Chantier C : construire l'audit_chain a partir de la
    # provenance recipes_web (recipe_used, sources Strate, polish LLM si
    # mode polished). L'audit_chain est stocke des la creation -- ainsi
    # l'invariant Principe 2 charte tient meme si l'Assembly est publie
    # sans passer par un tool publish_assembly cote agent.
    provenance = scene_manifest.get("provenance") or {}
    audit_chain = build_audit_chain_from_provenance(
        aid=aid,
        sid=sid,
        owner=owner,
        provenance=provenance,
        components_refs=persisted_cids,
        treatments_lines=None,  # Sprint V0.5 : lire treatments.jsonl workspace
        classification="cerema_internal",
    )

    assembly = Assembly(
        id=aid,
        version=1,
        sid=sid,
        kind="storymap_narrative_dsfr",
        title=title,
        description=provenance.get("recipe_used", {}).get("slug"),
        layout=AssemblyLayout(type="scroll_vertical", sections=[section]),
        footer=_default_footer(scene_manifest),
        audience="cerema_internal",
        audit_chain=audit_chain,
    )

    # 3. Persist row assemblies_index. L'audit_chain est serialise en JSON
    # pour le champ dedie (aligne pattern publish_assembly cote agent).
    import json
    audit_chain_json = json.dumps(
        audit_chain.model_dump(mode="json"),
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    await assemblies.insert_assembly(
        assembly=assembly,
        owner=owner,
        file_path="",
        rendered_path="",
        audit_chain_json=audit_chain_json,
        previous_hash="",
    )

    return aid, persisted_cids
