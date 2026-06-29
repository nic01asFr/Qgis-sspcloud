"""
Vague E2 Commit 3 (D-QGIS-009 §3, 2026-06-29) — patterns metier AssemblyKind.

6 compositions pre-faites de kinds atomiques qui correspondent a des
chapitres metier canoniques d'une storymap CEREMA. L'agent IA / Marie
invoque un pattern (ex: 'hero_constat') au lieu de re-composer les 4-6
kinds atomiques a chaque storymap.

Chaque pattern = recette JSON qui contient :
- description : 1 ligne pour l'agent IA, role narratif
- params_schema : params utilisateur requis (ex: {kpis: [...], source: str})
- components : N component manifests parametrables par les params
- section : AssemblySection template avec refs aux cid generes
- example : exemple complet d'usage

Workflow agent IA :
1. list_storymap_patterns() pour decouvrir
2. describe_storymap_pattern(name) pour voir la recette
3. Construire les N component manifests selon params user
4. create_component x N + ajouter section au assembly via update_assembly

Pas un nouveau AssemblyKind Literal : ces patterns produisent des
storymap_narrative_dsfr standards, juste avec une COMPOSITION canonique.
"""
from __future__ import annotations

from typing import Any


# ============================================================================
# Patterns metier — 6 chapitres canoniques d'une storymap CEREMA
# ============================================================================

PATTERNS: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------------
    # PATTERN 1 : HERO CONSTAT (ouverture immersion forte)
    # ------------------------------------------------------------------------
    "hero_constat": {
        "name": "hero_constat",
        "description": (
            "Ouverture immersive d'une storymap : un titre fort + 1-3 chiffres "
            "cles + source officielle. Use case : poser l'enjeu en 5 secondes "
            "de lecture. Section.kind = 'intro' pour rendu pleine largeur."
        ),
        "role_narratif": "impact / immersion",
        "params_schema": {
            "title": "str : titre principal du constat (ex: 'Risque inondation 4e arrondissement')",
            "subtitle": "str? : surtitre eyebrow (ex: 'Diagnostic 2026')",
            "kpis": "list[{value, label, unit?}] : 1-3 chiffres cles frappants",
            "source": "str : source datee (ex: 'BD TOPO IGN 2024 + Georisques TRI')",
        },
        "components_template": [
            {
                "kind": "heading",
                "title": "{title}",  # interpolation
                "params": {"text": "{title}", "level": 1},
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "kpi_grid",
                "title": "Chiffres-cles",
                "params": {
                    "kpis": "{kpis}",  # injecte directement
                    "palette": "monochrome",  # DSFR sobre default
                    "columns_min": 180,
                },
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "narrative_text",
                "title": "Source",
                "params": {"content": "Source : {source}"},
                "rendering": {"runtime": "marked", "container_size": "full"},
                "classification": "cerema_internal",
            },
        ],
        "section_template": {
            "kind": "intro",
            "title": None,  # le heading kind sert de titre
            "components": "{component_refs}",  # liste des refs cid generes
        },
        "example": {
            "title": "Risque inondation 4e arrondissement",
            "subtitle": "Diagnostic CEREMA 2026",
            "kpis": [
                {"value": "47", "label": "% du territoire", "unit": "%"},
                {"value": "5 670", "label": "batiments exposes"},
                {"value": "49 744", "label": "habitants concernes"},
            ],
            "source": "BD TOPO IGN 2024 + Georisques TRI",
        },
    },

    # ------------------------------------------------------------------------
    # PATTERN 2 : ZOOM TERRITOIRE (carte localisation + contexte)
    # ------------------------------------------------------------------------
    "zoom_territoire": {
        "name": "zoom_territoire",
        "description": (
            "Carte de localisation avec contexte territorial : titre + texte "
            "intro + interactive_map (zone d'etude) + legend. Use case : "
            "presenter le perimetre d'analyse. Trio cartographe obligatoire "
            "(titre + legende + source datee) - Vague E2 Commit 4."
        ),
        "role_narratif": "contexte / localisation",
        "params_schema": {
            "title": "str : titre de la carte (ex: 'Perimetre d'etude — 4e arrondissement')",
            "narrative_intro": "str : markdown intro contexte (1-3 paragraphes)",
            "scene_hash": "str : hash du scene_manifest de la carte",
            "sid": "str : etude id",
            "pid": "str : projet id",
            "legend_items": "list[{label, color, count?}] : entrees legende",
            "source": "str : source datee",
            "caveat": "str? : caveat methodologique (ex: 'donnees 2024, ne pas extrapoler')",
        },
        "components_template": [
            {
                "kind": "heading",
                "title": "{title}",
                "params": {"text": "{title}", "level": 2},
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "narrative_text",
                "title": "Contexte",
                "params": {"content": "{narrative_intro}"},
                "rendering": {"runtime": "marked", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "interactive_map",
                "title": "{title}",
                "source": {
                    "scope": "project",
                    "sid": "{sid}",
                    "pid": "{pid}",
                    "scene_hash": "{scene_hash}",
                },
                "params": {"caveat": "{caveat}"},  # rendu inline si present
                "rendering": {"runtime": "maplibre", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "legend",
                "title": "Legende",
                "params": {
                    "items": "{legend_items}",
                    "source": "{source}",
                },
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
        ],
        "section_template": {
            "kind": "section",
            "title": None,  # le heading sert de titre
            "components": "{component_refs}",
        },
    },

    # ------------------------------------------------------------------------
    # PATTERN 3 : CROISEMENT ENJEU (carte + chart + interpretation)
    # ------------------------------------------------------------------------
    "croisement_enjeu": {
        "name": "croisement_enjeu",
        "description": (
            "Croisement de donnees : titre + interactive_map + chart "
            "complementaire + narrative_text interpretation. Use case : "
            "demontrer un enjeu via croisement spatial+chiffres. Le chart "
            "DOIT illustrer ce que la carte montre (pas redondant)."
        ),
        "role_narratif": "analyse / croisement",
        "params_schema": {
            "title": "str : titre du croisement (ex: 'Exposition par scenario')",
            "narrative_intro": "str : 1 paragraphe contexte de la donnee croisee",
            "scene_hash": "str : carte du croisement",
            "sid": "str", "pid": "str",
            "chart_type": "str : 'bar' | 'line' | 'pie'",
            "chart_labels": "list[str] : axe X (ex: ['T10','T50','T100','T1000'])",
            "chart_datasets": "list[{label, data:[]}] : series chart",
            "narrative_interpretation": "str markdown : lecture du croisement (les enseignements)",
            "source": "str : source datee",
        },
        "components_template": [
            {
                "kind": "heading",
                "title": "{title}",
                "params": {"text": "{title}", "level": 2},
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "narrative_text",
                "params": {"content": "{narrative_intro}"},
                "rendering": {"runtime": "marked", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "interactive_map",
                "title": "{title} — vue cartographique",
                "source": {
                    "scope": "project", "sid": "{sid}", "pid": "{pid}",
                    "scene_hash": "{scene_hash}",
                },
                "rendering": {"runtime": "maplibre", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "chart",
                "title": "{title} — distribution",
                "params": {
                    "chart_type": "{chart_type}",
                    "labels": "{chart_labels}",
                    "datasets": "{chart_datasets}",
                    "source": "{source}",
                },
                "rendering": {"runtime": "chartjs", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "narrative_text",
                "title": "Interpretation",
                "params": {"content": "**Lecture :** {narrative_interpretation}"},
                "rendering": {"runtime": "marked", "container_size": "full"},
                "classification": "cerema_internal",
            },
        ],
        "section_template": {
            "kind": "section",
            "title": None,
            "components": "{component_refs}",
        },
    },

    # ------------------------------------------------------------------------
    # PATTERN 4 : FICHE INDICATEUR (kpi_grid + table + methodo + quote)
    # ------------------------------------------------------------------------
    "fiche_indicateur": {
        "name": "fiche_indicateur",
        "description": (
            "Fiche complete d'un indicateur : kpi_grid resultats + table "
            "donnees brutes + narrative methodo + quote expert. Use case : "
            "decrire en profondeur un indicateur metier (mortalite, "
            "vulnerabilite, exposition...). Lecteur ressort avec methodo "
            "validee + caveat + reference."
        ),
        "role_narratif": "fiche / decomposition",
        "params_schema": {
            "indicateur": "str : nom de l'indicateur (ex: 'Exposition piétons en zone inondable')",
            "kpis": "list[{value, label, unit?, color?}] : 3-6 KPIs (palette monochrome auto)",
            "table_columns": "list[{key, label}]",
            "table_rows": "list[dict]",
            "methodology": "str markdown : etapes de calcul methodologie (caveat inclus)",
            "expert_quote": "str : citation expert pour valider la methodo",
            "expert_name": "str : nom expert",
            "expert_role": "str : role expert (ex: 'Chef de projet risque, CEREMA')",
            "source": "str : source datee",
        },
        "components_template": [
            {
                "kind": "heading",
                "title": "Fiche : {indicateur}",
                "params": {"text": "Fiche : {indicateur}", "level": 2},
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "kpi_grid",
                "params": {"kpis": "{kpis}", "palette": "monochrome"},
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "data_table",
                "title": "Donnees brutes",
                "params": {
                    "columns": "{table_columns}",
                    "rows": "{table_rows}",
                    "source": "{source}",
                },
                "rendering": {"runtime": "datatables", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "narrative_text",
                "title": "Methodologie",
                "params": {"content": "### Methodologie\n\n{methodology}"},
                "rendering": {"runtime": "marked", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "quote",
                "params": {
                    "text": "{expert_quote}",
                    "author": "{expert_name}",
                    "source": "{expert_role}",
                },
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
        ],
        "section_template": {
            "kind": "section",
            "title": None,
            "components": "{component_refs}",
        },
    },

    # ------------------------------------------------------------------------
    # PATTERN 5 : RELIABILITY SUMMARY (matrice fiabilite + caveats)
    # ------------------------------------------------------------------------
    "reliability_summary": {
        "name": "reliability_summary",
        "description": (
            "Resume fiabilite des resultats : heading + narrative caveats + "
            "table 'indicateur / fiabilite / source / commentaire'. Use case : "
            "section critique qui aide le decideur a savoir QUOI CROIRE. "
            "OBLIGATOIRE pour livrable destine a un COPIL."
        ),
        "role_narratif": "verification / caveat / fiabilite",
        "params_schema": {
            "title": "str? : default 'Niveaux de fiabilite'",
            "preface": "str markdown : courte intro pourquoi cette section",
            "reliability_table": (
                "list[{indicateur, fiabilite ('haute'|'moyenne'|'basse'), "
                "source, commentaire}]"
            ),
            "global_caveat": "str? : caveat global (ex: 'donnees pedagogiques')",
        },
        "components_template": [
            {
                "kind": "heading",
                "title": "{title}",
                "params": {"text": "{title}", "level": 2},
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "narrative_text",
                "params": {"content": "{preface}"},
                "rendering": {"runtime": "marked", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "data_table",
                "title": "Niveaux de fiabilite par indicateur",
                "params": {
                    "columns": [
                        {"key": "indicateur", "label": "Indicateur"},
                        {"key": "fiabilite", "label": "Fiabilite"},
                        {"key": "source", "label": "Source"},
                        {"key": "commentaire", "label": "Commentaire"},
                    ],
                    "rows": "{reliability_table}",
                },
                "rendering": {"runtime": "datatables", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "quote",
                "params": {
                    "text": "{global_caveat}",
                    "author": "CEREMA",
                    "source": "Caveat methodologique",
                },
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
        ],
        "section_template": {
            "kind": "appendix",  # mis en annexe (typo reduite, sobre)
            "title": None,
            "components": "{component_refs}",
        },
    },

    # ------------------------------------------------------------------------
    # PATTERN 6 : CONCLUSION ACTIONNABLE (synthese + actions + quote decideur)
    # ------------------------------------------------------------------------
    "conclusion_actionnable": {
        "name": "conclusion_actionnable",
        "description": (
            "Conclusion qui rappelle l'enjeu + propose des actions concretes : "
            "narrative synthese + kpi_grid actions (3-5 leviers) + quote "
            "decideur + button telechargement PDF. Use case : terminer la "
            "storymap par 'que retenir ET que faire'."
        ),
        "role_narratif": "synthese / appel a action",
        "params_schema": {
            "title": "str? : default 'Conclusion et actions'",
            "synthese": "str markdown : 2-3 paragraphes recap des enseignements",
            "actions": "list[{value, label}] : 3-5 leviers/actions concretes",
            "quote_decideur": "str : citation decideur engageant CEREMA",
            "decideur_name": "str",
            "decideur_role": "str",
            "pdf_url": "str? : URL telechargement PDF complet",
        },
        "components_template": [
            {
                "kind": "heading",
                "title": "{title}",
                "params": {"text": "{title}", "level": 2},
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "narrative_text",
                "title": "Synthese",
                "params": {"content": "{synthese}"},
                "rendering": {"runtime": "marked", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "kpi_grid",
                "title": "Leviers d'action",
                "params": {
                    "kpis": "{actions}",
                    "palette": "monochrome",  # sobre, leviers numerotes
                    "columns_min": 200,
                },
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
            {
                "kind": "quote",
                "params": {
                    "text": "{quote_decideur}",
                    "author": "{decideur_name}",
                    "source": "{decideur_role}",
                },
                "rendering": {"runtime": "html", "container_size": "full"},
                "classification": "cerema_internal",
            },
        ],
        "section_template": {
            "kind": "conclusion",  # rendu call-out avec border bleu Marianne
            "title": None,
            "components": "{component_refs}",
        },
    },
}


def list_patterns() -> dict[str, dict[str, str]]:
    """Liste light des patterns (name, description, role_narratif).

    Use case : agent IA fait `list_storymap_patterns()` pour decouvrir.
    """
    return {
        name: {
            "name": p["name"],
            "description": p["description"],
            "role_narratif": p["role_narratif"],
            "n_components": len(p["components_template"]),
            "section_kind": p["section_template"]["kind"],
        }
        for name, p in PATTERNS.items()
    }


def describe_pattern(name: str) -> dict[str, Any]:
    """Recette complete d'un pattern.

    Use case : agent IA fait `describe_storymap_pattern('hero_constat')`
    pour voir le template params + components a creer + section finale.
    """
    if name not in PATTERNS:
        raise ValueError(
            f"Pattern '{name}' inconnu. "
            f"Disponibles : {sorted(PATTERNS.keys())}"
        )
    return PATTERNS[name]


def get_pattern_names() -> list[str]:
    """Liste des noms de patterns disponibles."""
    return sorted(PATTERNS.keys())
