"""
Sérialisation bi-directionnelle Assembly Pydantic <-> BlockNote JSON.

Vague E2 Commit G (D-QGIS-010). Permet à l'éditeur BlockNote de sauver
ses modifs côté hub avec validation Pydantic Assembly.

Mapping :
  Assembly.layout.sections[]                 <-> BlockNote document blocks
    section.title (str)                       -> heading H2 natif
    section.narrative_md (str)                -> paragraph natif
    section.components[{ref: cid}]            -> custom block du kind correspondant

Custom blocks via componentKindToBlockType :
  ComponentKind     <-> BlockNote block type
  kpi_grid          <-> kpiGrid
  heading           <-> customHeading
  quote             <-> customQuote
  separator         <-> separator
  kpi_badge         <-> kpiBadge
  legend            <-> legend
  narrative_text    <-> narrativeText
  interactive_map   <-> interactiveMap
  chart             <-> chart
  data_table        <-> dataTable
  scene_3d          <-> scene3d
  media_embed       <-> mediaEmbed
  iframe_grist      <-> iframeGrist

V1 : forward (Assembly -> BlockNote) côté React, backward (BlockNote ->
Assembly) côté Python (ici). Tests round-trip pytest pour valider
losslessness.
"""
from __future__ import annotations

from typing import Any


BLOCK_TYPE_TO_COMPONENT_KIND: dict[str, str] = {
    "kpiGrid": "kpi_grid",
    "customHeading": "heading",
    "customQuote": "quote",
    "separator": "separator",
    "kpiBadge": "kpi_badge",
    "legend": "legend",
    "narrativeText": "narrative_text",
    "interactiveMap": "interactive_map",
    "chart": "chart",
    "dataTable": "data_table",
    "scene3d": "scene_3d",
    "mediaEmbed": "media_embed",
    "iframeGrist": "iframe_grist",
}

# Sentinel : custom block type qui re-déclenche une nouvelle section
SECTION_BREAK_BLOCK_TYPES = ("heading",)  # heading natif level 1-2 = nouveau bloc


def block_to_component_params(block: dict[str, Any]) -> dict[str, Any] | None:
    """Convertit un block BlockNote en {kind, params} pour Component manifest.

    Retourne None si le block n'a pas de mapping (paragraph natif sans cid,
    ou bloc non-custom).
    """
    btype = block.get("type", "")
    kind = BLOCK_TYPE_TO_COMPONENT_KIND.get(btype)
    if not kind:
        return None
    props = block.get("props", {}) or {}

    # Mapping inverse props -> params selon kind
    if kind == "kpi_grid":
        try:
            kpis_raw = props.get("kpisJson", "[]")
            kpis = kpis_raw if isinstance(kpis_raw, list) else _parse_json_list(kpis_raw)
        except Exception:
            kpis = []
        return {
            "kind": kind,
            "params": {
                "kpis": kpis,
                "palette": props.get("palette", "monochrome"),
                "columns_min": int(props.get("columnsMin", 140)),
            },
        }
    if kind == "heading":
        return {
            "kind": kind,
            "params": {
                "text": props.get("text", ""),
                "level": int(props.get("level", 2)),
            },
        }
    if kind == "kpi_badge":
        return {
            "kind": kind,
            "params": {
                "value": props.get("value", ""),
                "label": props.get("label", ""),
                "unit": props.get("unit", ""),
                "color": props.get("color", "info-blue"),
                "source": props.get("source", ""),
            },
        }
    if kind == "quote":
        return {
            "kind": kind,
            "params": {
                "text": props.get("text", ""),
                "author": props.get("author", ""),
                "source": props.get("source", ""),
            },
        }
    if kind == "separator":
        return {
            "kind": kind,
            "params": {
                "style": props.get("style", "solid"),
                "color": props.get("color", "#000091"),
                "variant": props.get("variant", "rule"),
            },
        }
    if kind == "legend":
        try:
            items_raw = props.get("itemsJson", "[]")
            items = items_raw if isinstance(items_raw, list) else _parse_json_list(items_raw)
        except Exception:
            items = []
        return {
            "kind": kind,
            "params": {
                "items": items,
                "title": props.get("title", ""),
                "source": props.get("source", ""),
            },
        }
    if kind == "narrative_text":
        return {
            "kind": kind,
            "params": {"content": props.get("content", "")},
        }
    # Iframe kinds : V1 = ref vers component existant, pas de params à rétroconvertir
    if kind in ("interactive_map", "chart", "data_table", "scene_3d",
                "media_embed", "iframe_grist"):
        # Pour ces kinds, l'éditeur référence un component existant via cid.
        # Le serializer ne peut pas reconstruire params (ils sont déjà côté hub).
        # Retourne juste {ref: cid} = pas de nouveau component à créer.
        return {"kind": kind, "ref_only": True, "cid": props.get("cid", "")}
    return None


def _parse_json_list(raw: Any) -> list:
    """Parse un JSON list string ou retourne [] en cas d'erreur."""
    import json
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        return []
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def blocknote_doc_to_assembly_sections(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convertit un BlockNote document en (sections, new_components).

    Regroupe les blocks en sections selon le pattern :
    - heading natif level <=2 -> nouvelle section (title = heading text)
    - paragraph natif -> ajouté à narrative_md de la section courante
    - custom block -> nouveau Component (DOM) ou ref vers existant (iframe)

    Retourne :
        sections : list[AssemblySection dict] prêt pour layout.sections
        new_components : list[Component manifest] à créer côté hub
                         AVANT update_assembly (DOM kinds uniquement, iframe
                         kinds référencent un cid existant)
    """
    sections: list[dict[str, Any]] = []
    new_components: list[dict[str, Any]] = []
    current_section: dict[str, Any] = {
        "kind": "section",
        "title": None,
        "narrative_md": None,
        "components": [],
    }

    def flush_current():
        nonlocal current_section
        has_content = (
            current_section.get("title")
            or current_section.get("narrative_md")
            or current_section.get("components")
        )
        if has_content:
            sections.append(current_section)
        current_section = {
            "kind": "section",
            "title": None,
            "narrative_md": None,
            "components": [],
        }

    for block in blocks:
        btype = block.get("type", "")

        # Heading natif H1/H2 -> nouvelle section
        if btype == "heading":
            level = (block.get("props", {}) or {}).get("level", 2)
            if int(level) <= 2:
                flush_current()
                # Title = textuel content du heading
                content = block.get("content", [])
                if isinstance(content, str):
                    current_section["title"] = content
                elif isinstance(content, list):
                    text_parts = []
                    for c in content:
                        if isinstance(c, dict) and "text" in c:
                            text_parts.append(str(c["text"]))
                        elif isinstance(c, str):
                            text_parts.append(c)
                    current_section["title"] = " ".join(text_parts).strip()
                continue

        # Paragraph natif -> accumulé dans narrative_md
        if btype == "paragraph":
            content = block.get("content", [])
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, dict) and "text" in c:
                        parts.append(str(c["text"]))
                    elif isinstance(c, str):
                        parts.append(c)
                text = " ".join(parts).strip()
            if text:
                existing = current_section.get("narrative_md") or ""
                current_section["narrative_md"] = (
                    existing + ("\n\n" if existing else "") + text
                )
            continue

        # Custom block -> Component
        result = block_to_component_params(block)
        if result is None:
            continue

        if result.get("ref_only"):
            # Iframe kind : ref vers component existant (cid déjà côté hub)
            cid = result.get("cid", "")
            if cid:
                current_section["components"].append({"ref": cid})
        else:
            # DOM kind : nouveau Component à créer côté hub
            # On enregistre dans new_components, le caller créera via create_component
            # et obtiendra un cid pour update_assembly. V1 : on stocke avec un
            # placeholder __pending__ que le caller remplacera.
            component_manifest = {
                "kind": result["kind"],
                "title": result.get("title", result["kind"]),
                "classification": "cerema_internal",
                "params": result["params"],
                "rendering": {
                    "runtime": "html",
                    "container_size": "full",
                },
            }
            new_components.append(component_manifest)
            current_section["components"].append({"ref": "__pending__"})

    # Flush dernière section
    flush_current()

    return sections, new_components
