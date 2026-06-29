# Composants Vague E1 — Specs techniques

> **Auteur** : Components-Designer · **Date** : 2026-06-29
> **Objectif** : "Résultat final professionnel" — 5 nouveaux kinds + 2 extensions schemas existants
> **Référence visuelle** : `storymap_blancarde_chartreux (24).html`
> **Backward-compat** : tous les champs ajoutés sont optionnels (defaults vides)

---

## Table des matières

1. [kpi_grid](#1-kpi_grid) — Bandeau N chiffres clés
2. [audit_chain_narrative](#2-audit_chain_narrative) — Pipeline visuel chaînes de traitement
3. [chapter_section](#3-chapter_section) — Chapitre avec fr-tabs DSFR
4. [reliability_matrix](#4-reliability_matrix) — Matrice fiabilité 3 niveaux
5. [pipeline_signature](#5-pipeline_signature) — Footer signé enrichi
6. [Extension AuditChain](#6-extension-schema-auditchain) — Champs Pydantic optionnels
7. [Extension narrative_text](#7-extension-narrative_text-kpi-inline) — KPI inline markdown

---

## 1. kpi_grid

**Rôle** : Bandeau responsive de N chiffres clés (remplace N `kpi_badge` individuels).
Référence Blancarde : 6 KPIs (2.89 km², 49,744 hab., 74.7 km voirie, 1,261 troncons, 63 pts contact, 25 a ouvrir).

### 1.1 Pydantic (extension component.py)

```python
# Ajout dans ComponentKind Literal
ComponentKind = Literal[
    # ... existants ...
    "kpi_grid",
]

# Ajout dans ComponentRendering.runtime Literal
"html",  # kpi_grid reutilise runtime "html"
```

**Schema params kpi_grid** :

```python
class KpiItem(BaseModel):
    """Un chiffre cle dans le bandeau."""
    value: str = Field(..., description="Valeur affichee (ex: '2.89', '49,744')")
    label: str = Field(..., description="Libelle court (ex: 'km2', 'hab.')")
    unit: str | None = Field(None, description="Unite optionnelle separee (ex: 'km')")
    trend: Literal["up", "down", "stable"] | None = Field(
        None, description="Tendance optionnelle (fleche indicative)"
    )
    color: str | None = Field(None, description="Override couleur token (ex: 'marianne-red')")

# Dans Component.params pour kind=kpi_grid :
# {
#   "kpis": [KpiItem, ...],
#   "columns_min": 110,    # optionnel, defaut 110px
#   "style": "default"     # optionnel, "default" | "compact" | "hero"
# }
```

### 1.2 Template partial `_kpi_grid_partial.j2`

```jinja2
{# Partial _kpi_grid — bandeau N chiffres cles responsive
   Vague E1. Grid auto-fill minmax(110px, 1fr).

   Variables :
   - kpis : list[dict] {value, label, unit?, trend?, color?}
   - columns_min : int (defaut 110)
   - style : str ("default"|"compact"|"hero")
#}
{% set col_min = columns_min | default(110) %}
{% set grid_style = style | default('default') %}
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax({{ col_min }}px,1fr));
            gap:8px;margin:12px 0">
  {% for kpi in kpis %}
  <div style="text-align:center;padding:{% if grid_style == 'hero' %}24px 12px{% elif grid_style == 'compact' %}8px 6px{% else %}12px 8px{% endif %}">
    <div style="font-size:{% if grid_style == 'hero' %}32px{% elif grid_style == 'compact' %}18px{% else %}22px{% endif %};
                font-weight:700;color:var(--text-title-blue-france,#000091);line-height:1.2">
      {{ kpi.value | e }}{% if kpi.unit %}<span style="font-size:60%;font-weight:400;margin-left:2px">{{ kpi.unit | e }}</span>{% endif %}
      {% if kpi.trend == 'up' %}<span style="color:#1f8d4d;font-size:60%">&#9650;</span>
      {% elif kpi.trend == 'down' %}<span style="color:#e1000f;font-size:60%">&#9660;</span>
      {% elif kpi.trend == 'stable' %}<span style="color:#666;font-size:60%">&#9654;</span>{% endif %}
    </div>
    <div style="font-size:{% if grid_style == 'compact' %}9px{% else %}10px{% endif %};
                color:var(--text-mention-grey,#666);text-transform:uppercase;
                letter-spacing:.5px;margin-top:4px">
      {{ kpi.label | e }}
    </div>
  </div>
  {% endfor %}
</div>
```

### 1.3 Helper render (main.py `_pre_render_component_html`)

```python
elif kind == "kpi_grid":
    kpis = params.get("kpis", []) or []
    tpl = _maplibre_jinja.get_template("_kpi_grid_partial.j2")
    return tpl.render(
        kpis=kpis,
        columns_min=params.get("columns_min", 110),
        style=params.get("style", "default"),
    )
```

### 1.4 Exemple manifest JSON

```json
{
  "id": "a1b2c3d4e5f6",
  "version": 1,
  "kind": "kpi_grid",
  "title": "Chiffres cles Blancarde-Chartreux",
  "source": {"scope": "project", "sid": "abcdef012345", "pid": "fedcba543210"},
  "params": {
    "kpis": [
      {"value": "2.89", "label": "km2"},
      {"value": "49,744", "label": "hab."},
      {"value": "74.7", "label": "km voirie"},
      {"value": "1,261", "label": "troncons"},
      {"value": "63", "label": "pts contact"},
      {"value": "25", "label": "a ouvrir", "trend": "down"}
    ]
  },
  "rendering": {"runtime": "html", "container_size": "responsive", "theme": "dsfr"}
}
```

---

## 2. audit_chain_narrative

**Rôle** : Pipeline visuel multi-chaines, consommant `assembly.audit_chain`. Chaque chaine = N etapes horizontales (source -> transform -> result) avec fleches separateurs, cliquable pour detail narratif.

Référence Blancarde : 5 chaines (circulation, foncier, centralite, canyon, terrain), chacune 2-4 etapes avec SVG flow + steps cliquables + detail narratif + badges.

### 2.1 Pydantic

```python
class ChainStep(BaseModel):
    """Une etape dans une chaine de traitement."""
    kind: Literal["source", "transform", "result"] = "transform"
    label: str = Field(..., description="Libelle court (ex: 'BD TOPO voirie')")
    n_features: int | None = Field(None, description="Nombre d'objets (optionnel)")
    icon: str | None = Field(None, description="Emoji ou URL icone (optionnel)")
    title: str | None = Field(None, description="Titre detail cliquable")
    text: str | None = Field(None, description="Texte narratif detail")
    data_badge: str | None = Field(None, description="Badge donnees (ex: '1261 troncons')")
    algo_badge: str | None = Field(None, description="Badge algo (ex: 'NetworkX BC')")
    result_badge: str | None = Field(None, description="Badge resultat (ex: '5 classes')")


class ChainGroup(BaseModel):
    """Une chaine de traitement nommee."""
    name: str = Field(..., description="ID unique (ex: 'circulation')")
    title: str = Field(..., description="Titre affiche (ex: 'Circulation & impasses')")
    narrative: str | None = Field(None, description="Resume narratif de la chaine")
    steps: list[ChainStep] = Field(default_factory=list)

# Dans Component.params pour kind=audit_chain_narrative :
# {
#   "chain_groups": [ChainGroup, ...],
#   "collapsed": true  # optionnel, defaut true (accordeons fermes)
# }
```

### 2.2 Template partial `_audit_chain_narrative_partial.j2`

```jinja2
{# Partial _audit_chain_narrative — pipeline visuel multi-chaines
   Vague E1. Accordeon + steps cliquables + detail narratif.

   Variables :
   - chain_groups : list[dict] {name, title, narrative?, steps: [{kind, label, ...}]}
   - collapsed : bool (defaut true)
   - cid : str
#}
{% set default_collapsed = collapsed | default(true) %}
<div>
{% for chain in chain_groups %}
{% set chain_id = cid[:8] ~ '-' ~ chain.name %}
<div style="margin:24px 0;background:#fff;border:1px solid #ddd;border-radius:4px;overflow:hidden">
  <div style="padding:14px 18px;background:#f6f6f6;border-bottom:1px solid #ddd;
              cursor:pointer;display:flex;align-items:center;gap:10px"
       onclick="(function(id){var b=document.getElementById('body-'+id);var h=b.parentElement.querySelector('[aria-expanded]');if(b.style.display==='block'){b.style.display='none';h.setAttribute('aria-expanded','false')}else{b.style.display='block';h.setAttribute('aria-expanded','true')}}('{{ chain_id }}'))"
       aria-expanded="{{ 'false' if default_collapsed else 'true' }}">
    <span style="transition:transform .2s;color:#000091;font-weight:700">&#9654;</span>
    <h4 style="font-size:14px;font-weight:700;color:#000091;margin:0;flex:1">
      {{ chain.title | e }}
    </h4>
    <span style="font-size:12px;color:#666">{{ chain.steps | length }} etapes</span>
  </div>
  <div id="body-{{ chain_id }}" style="display:{{ 'none' if default_collapsed else 'block' }};padding:18px">
    {# Steps horizontaux avec fleches #}
    <div style="display:flex;gap:4px;overflow-x:auto;padding:4px 0">
      {% for step in chain.steps %}
      {% if not loop.first %}
      <div style="flex:0 0 20px;display:flex;align-items:center;justify-content:center;
                  color:#000091;font-size:18px;font-weight:700">&#8594;</div>
      {% endif %}
      <div style="flex:0 0 auto;width:140px;border:2px solid {% if step.kind == 'source' %}#1f8d4d{% elif step.kind == 'result' %}#e1000f{% else %}#ddd{% endif %};
                  border-radius:4px;padding:8px;text-align:center;background:#fff;cursor:pointer"
           onclick="(function(cid,idx){var d=document.getElementById('detail-'+cid);if(d){d.innerHTML='<h4 style=font-size:14px;font-weight:700;margin:0>{{ step.title | default(step.label) | e }}</h4><p style=font-size:13px;color:#666;margin:8px+0;line-height:1.5>{{ step.text | default('') | e }}</p><div style=display:flex;gap:6px;flex-wrap:wrap>{% if step.data_badge %}<span style=display:inline-block;padding:2px+8px;background:#e5edf5;border-radius:10px;font-size:11px;color:#000091>{{ step.data_badge | e }}</span>{% endif %}{% if step.algo_badge %}<span style=display:inline-block;padding:2px+8px;background:#fef4e5;border-radius:10px;font-size:11px;color:#b34000>{{ step.algo_badge | e }}</span>{% endif %}{% if step.result_badge %}<span style=display:inline-block;padding:2px+8px;background:#e5f5e5;border-radius:10px;font-size:11px;color:#1f6d3d>{{ step.result_badge | e }}</span>{% endif %}</div>'}}('{{ chain_id }}',{{ loop.index0 }}))">
        {% if step.icon %}
        <div style="font-size:24px;margin-bottom:4px">{{ step.icon }}</div>
        {% endif %}
        <div style="font-size:10px;color:#666;font-weight:500">{{ step.label | e }}</div>
        {% if step.n_features %}
        <div style="font-size:9px;color:#999;margin-top:2px">{{ step.n_features }} obj.</div>
        {% endif %}
      </div>
      {% endfor %}
    </div>
    {# Zone detail cliquable #}
    <div id="detail-{{ chain_id }}" style="margin-top:14px;padding:14px;background:#f6f6f6;
                                           border-radius:4px;font-size:13px;color:#666">
      {% if chain.narrative %}{{ chain.narrative | e }}{% else %}Cliquez une etape pour voir le detail.{% endif %}
    </div>
  </div>
</div>
{% endfor %}
</div>
```

### 2.3 Helper render (main.py)

```python
elif kind == "audit_chain_narrative":
    chain_groups = params.get("chain_groups", []) or []
    # Backward-compat : si chain_groups vide, genere auto depuis
    # audit_chain.tool_calls_made groupe par recipe_used
    if not chain_groups:
        # Tentative auto-generation depuis assembly audit_chain
        # (disponible si comp_manifest inclut _audit_chain_ref)
        audit_ref = comp_manifest.get("_audit_chain_ref", {})
        if audit_ref:
            from collections import defaultdict
            groups_map = defaultdict(list)
            for tc in (audit_ref.get("tool_calls_made") or []):
                recipe = tc.get("recipe_used", "general")
                groups_map[recipe].append({
                    "kind": "transform",
                    "label": tc.get("tool_name", tc.get("name", "?")),
                    "n_features": tc.get("n_out"),
                })
            chain_groups = [
                {"name": k, "title": k.replace("_", " ").title(), "steps": v}
                for k, v in groups_map.items()
            ]
    tpl = _maplibre_jinja.get_template("_audit_chain_narrative_partial.j2")
    return tpl.render(
        chain_groups=chain_groups,
        collapsed=params.get("collapsed", True),
        cid=cid,
    )
```

### 2.4 Exemple manifest JSON

```json
{
  "id": "c1d2e3f4a5b6",
  "version": 1,
  "kind": "audit_chain_narrative",
  "title": "Construction de l'analyse",
  "source": {"scope": "project", "sid": "abcdef012345", "pid": "fedcba543210"},
  "params": {
    "collapsed": true,
    "chain_groups": [
      {
        "name": "circulation",
        "title": "Circulation & impasses",
        "narrative": "Analyse du reseau viaire : types de voies, sens, impasses multi-modales.",
        "steps": [
          {
            "kind": "source",
            "label": "BD TOPO voirie",
            "n_features": 1261,
            "icon": "📥",
            "title": "Donnees source",
            "text": "Import BD TOPO v3 voirie — 1261 troncons dans le perimetre.",
            "data_badge": "1261 troncons"
          },
          {
            "kind": "transform",
            "label": "Typage multi-modal",
            "icon": "⚙️",
            "title": "Classification voies",
            "text": "Chaque troncon recoit un type par mode (voiture/velo/pieton).",
            "algo_badge": "3 modes"
          },
          {
            "kind": "transform",
            "label": "Detection impasses",
            "icon": "⚙️",
            "title": "Impasses multi-modales",
            "text": "Degree-1 par mode. 35 voies impasse voiture avec passage pieton.",
            "algo_badge": "NetworkX degree"
          },
          {
            "kind": "result",
            "label": "Carte circulation",
            "icon": "📊",
            "title": "Resultat",
            "text": "Carte interactive avec filtrage par mode de deplacement.",
            "result_badge": "3 couches"
          }
        ]
      },
      {
        "name": "foncier",
        "title": "Foncier & points de contact",
        "narrative": "Identification des emprises potentiellement privees et points de contact publics/prives.",
        "steps": [
          {"kind": "source", "label": "PCI parcellaire", "icon": "📥", "n_features": 2800},
          {"kind": "transform", "label": "Jointure voirie-parcelles", "icon": "⚙️"},
          {"kind": "transform", "label": "Classification domanialite", "icon": "⚙️", "algo_badge": "9 niveaux"},
          {"kind": "result", "label": "63 pts contact", "icon": "📊", "result_badge": "25 a ouvrir"}
        ]
      }
    ]
  },
  "rendering": {"runtime": "html", "container_size": "responsive", "theme": "dsfr"}
}
```

---

## 3. chapter_section

**Rôle** : Chapitre structure avec onglets DSFR `fr-tabs`. Permet navigation entre sous-sujets (ex: Chapitre 1 — Le reseau : Circulation | Centralite | Velo & pentes).

Référence Blancarde : 3 chapitres avec 2-3 onglets chacun.

### 3.1 Pydantic

```python
class TabEntry(BaseModel):
    """Un onglet dans un chapter_section."""
    label: str = Field(..., description="Libelle onglet (ex: 'Circulation')")
    components: list[dict] = Field(
        default_factory=list,
        description="Refs composants dans l'onglet [{ref: cid}]"
    )

# Dans Component.params pour kind=chapter_section :
# {
#   "title": "Le reseau",
#   "chapter_num": 1,        # optionnel, pour prefixe "Chapitre N —"
#   "tabs": [TabEntry, ...],
#   "background": "alt"      # optionnel, "alt" | "default"
# }
```

### 3.2 Template partial `_chapter_section_partial.j2`

```jinja2
{# Partial _chapter_section — chapitre avec fr-tabs DSFR
   Vague E1. Navigation entre sous-sujets dans un chapitre.

   Variables :
   - cid : str
   - title : str
   - chapter_num : int|None
   - tabs : list[dict] {label, components_html: str}
   - background : str ("alt"|"default")
#}
{% set bg = 'var(--background-alt-grey,#f6f6f6)' if background == 'alt' else '#fff' %}
{% set section_id = 'chap_' ~ cid[:8] %}
<section style="padding:48px 0;background:{{ bg }}">
<div style="max-width:1200px;margin:0 auto;padding:0 24px">
  <p style="font-size:18px;font-weight:700;color:#000091;
            border-bottom:3px solid #000091;padding-bottom:8px;margin-bottom:16px">
    {% if chapter_num %}Chapitre {{ chapter_num }} — {% endif %}{{ title | e }}
  </p>
  {% if tabs | length > 1 %}
  <div class="fr-tabs">
    <ul class="fr-tabs__list" role="tablist" aria-label="{{ title | e }}">
      {% for tab in tabs %}
      <li role="presentation">
        <button id="tab-{{ section_id }}-{{ loop.index0 }}"
                class="fr-tabs__tab"
                tabindex="{{ 0 if loop.first else -1 }}"
                role="tab"
                aria-selected="{{ 'true' if loop.first else 'false' }}"
                aria-controls="panel-{{ section_id }}-{{ loop.index0 }}">
          {{ tab.label | e }}
        </button>
      </li>
      {% endfor %}
    </ul>
    {% for tab in tabs %}
    <div id="panel-{{ section_id }}-{{ loop.index0 }}"
         class="fr-tabs__panel{% if loop.first %} fr-tabs__panel--selected{% endif %}"
         role="tabpanel"
         aria-labelledby="tab-{{ section_id }}-{{ loop.index0 }}">
      {{ tab.components_html | safe }}
    </div>
    {% endfor %}
  </div>
  {% elif tabs | length == 1 %}
  {# Un seul onglet : pas de tabs, contenu direct #}
  {{ tabs[0].components_html | safe }}
  {% endif %}
</div>
</section>
```

### 3.3 Helper render (main.py)

```python
elif kind == "chapter_section":
    tabs_raw = params.get("tabs", []) or []
    # Chaque tab.components contient [{ref: cid}] — pre-rendre recursivement
    rendered_tabs = []
    for tab in tabs_raw:
        tab_html_parts = []
        for comp_ref in (tab.get("components") or []):
            ref_cid = comp_ref.get("ref", "")
            if ref_cid and ref_cid in rendered_components:
                tab_html_parts.append(rendered_components[ref_cid])
            elif ref_cid:
                tab_html_parts.append(
                    f'<div style="padding:20px;color:#666;font-style:italic">'
                    f'Composant {ref_cid[:8]} non rendu.</div>'
                )
        rendered_tabs.append({
            "label": tab.get("label", ""),
            "components_html": "\n".join(tab_html_parts),
        })
    tpl = _maplibre_jinja.get_template("_chapter_section_partial.j2")
    return tpl.render(
        cid=cid,
        title=params.get("title", ""),
        chapter_num=params.get("chapter_num"),
        tabs=rendered_tabs,
        background=params.get("background", "default"),
    )
```

> **Note** : `chapter_section` a acces a `rendered_components` (dict cid->html des composants deja rendus). Il faut passer ce dict comme parametre supplementaire de `_pre_render_component_html` quand `kind == "chapter_section"`. Signature etendue :
>
> ```python
> async def _pre_render_component_html(
>     comp_manifest: dict,
>     sid: str,
>     username: str,
>     cid: str,
>     rendered_components: dict[str, str] | None = None,  # Vague E1
> ) -> str:
> ```

### 3.4 Exemple manifest JSON

```json
{
  "id": "d1e2f3a4b5c6",
  "version": 1,
  "kind": "chapter_section",
  "title": "Le reseau",
  "source": {"scope": "project", "sid": "abcdef012345", "pid": "fedcba543210"},
  "params": {
    "title": "Le reseau",
    "chapter_num": 1,
    "background": "alt",
    "tabs": [
      {
        "label": "Circulation",
        "components": [
          {"ref": "comp_narrative_circ"},
          {"ref": "comp_map_circ"},
          {"ref": "comp_legend_circ"}
        ]
      },
      {
        "label": "Centralite",
        "components": [
          {"ref": "comp_narrative_cent"},
          {"ref": "comp_map_cent"}
        ]
      },
      {
        "label": "Velo & pentes",
        "components": [
          {"ref": "comp_narrative_velo"},
          {"ref": "comp_map_velo"}
        ]
      }
    ]
  },
  "rendering": {"runtime": "html", "container_size": "responsive", "theme": "dsfr"}
}
```

---

## 4. reliability_matrix

**Rôle** : Tableau 3 niveaux de fiabilite par variable (HAUTE / MOYENNE / BASSE).

Référence Blancarde : accordeon "Fiabilite" avec badges colores (success/warning/error) par variable.

### 4.1 Pydantic

```python
class ReliabilityRow(BaseModel):
    """Une ligne dans la matrice de fiabilite."""
    variable: str = Field(..., description="Nom de la variable (ex: 'Voirie')")
    level: Literal["HAUTE", "MOYENNE", "BASSE"] = "MOYENNE"
    reason: str | None = Field(None, description="Justification (ex: 'Source officielle IGN v3')")

# Dans Component.params pour kind=reliability_matrix :
# {
#   "rows": [ReliabilityRow, ...],
#   "title": "Fiabilite des variables"   # optionnel
# }
```

### 4.2 Template partial `_reliability_matrix_partial.j2`

```jinja2
{# Partial _reliability_matrix — tableau 3 niveaux par variable
   Vague E1. Couleurs DSFR : success/warning/error.

   Variables :
   - rows : list[dict] {variable, level: 'HAUTE'|'MOYENNE'|'BASSE', reason?}
   - title : str optionnel
#}
{% set level_style = {
  'HAUTE': 'background:#b8fec9;color:#18753c;border-color:#18753c',
  'MOYENNE': 'background:#ffe9c5;color:#b34000;border-color:#b34000',
  'BASSE': 'background:#fec5c5;color:#ce0500;border-color:#ce0500'
} %}
<div style="background:#fff;border:1px solid #e5e5e5;border-radius:6px;overflow:hidden">
  {% if title %}
  <div style="padding:16px 20px;border-bottom:1px solid #e5e5e5;background:#f6f6f6">
    <h3 style="margin:0;color:#000091;font-size:15px;font-weight:700">{{ title | e }}</h3>
  </div>
  {% endif %}
  <table style="width:100%;border-collapse:collapse;font-size:13px;font-family:Marianne,system-ui,sans-serif">
    <thead>
      <tr style="background:#f6f6f6">
        <th style="text-align:left;padding:10px 16px;font-weight:700;color:#000091;
                   font-size:11px;text-transform:uppercase;letter-spacing:.4px;
                   border-bottom:2px solid #000091">Variable</th>
        <th style="text-align:center;padding:10px 16px;font-weight:700;color:#000091;
                   font-size:11px;text-transform:uppercase;letter-spacing:.4px;
                   border-bottom:2px solid #000091;width:120px">Niveau</th>
        <th style="text-align:left;padding:10px 16px;font-weight:700;color:#000091;
                   font-size:11px;text-transform:uppercase;letter-spacing:.4px;
                   border-bottom:2px solid #000091">Justification</th>
      </tr>
    </thead>
    <tbody>
      {% for row in rows %}
      <tr style="border-bottom:1px solid #f0f0f0">
        <td style="padding:10px 16px;font-weight:600;color:#161616">{{ row.variable | e }}</td>
        <td style="padding:10px 16px;text-align:center">
          <span style="display:inline-block;padding:2px 10px;border-radius:10px;
                       font-size:11px;font-weight:700;
                       {{ level_style.get(row.level, level_style.MOYENNE) }}">
            {{ row.level | e }}
          </span>
        </td>
        <td style="padding:10px 16px;color:#666;font-size:12px">
          {{ row.reason | default('') | e }}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

### 4.3 Helper render (main.py)

```python
elif kind == "reliability_matrix":
    rows = params.get("rows", []) or []
    tpl = _maplibre_jinja.get_template("_reliability_matrix_partial.j2")
    return tpl.render(
        rows=rows,
        title=params.get("title", ""),
    )
```

### 4.4 Exemple manifest JSON

```json
{
  "id": "e1f2a3b4c5d6",
  "version": 1,
  "kind": "reliability_matrix",
  "title": "Fiabilite des variables",
  "source": {"scope": "project", "sid": "abcdef012345", "pid": "fedcba543210"},
  "params": {
    "title": "Fiabilite des variables",
    "rows": [
      {"variable": "Voirie", "level": "HAUTE", "reason": "BD TOPO v3 IGN — source officielle"},
      {"variable": "Orientation", "level": "HAUTE", "reason": "Calcul geometrique deterministe"},
      {"variable": "Pts contact", "level": "HAUTE", "reason": "Intersection spatiale + Panoramax"},
      {"variable": "Centralite orientee", "level": "HAUTE", "reason": "NetworkX betweenness_centrality"},
      {"variable": "Impasses", "level": "MOYENNE", "reason": "Degree-1 graph — faux positifs possibles aux limites"},
      {"variable": "Diagnostic", "level": "MOYENNE", "reason": "Heuristique multi-critere"},
      {"variable": "Cyclable", "level": "MOYENNE", "reason": "OSM + schemas amenagement"},
      {"variable": "Canyon", "level": "MOYENNE", "reason": "MNS non disponible — proxy RGE ALTI"},
      {"variable": "Commerces OSM", "level": "BASSE", "reason": "Couverture OSM heterogene"},
      {"variable": "Vegetation OSM", "level": "BASSE", "reason": "Tags OSM non exhaustifs"}
    ]
  },
  "rendering": {"runtime": "html", "container_size": "responsive", "theme": "dsfr"}
}
```

---

## 5. pipeline_signature

**Rôle** : Footer signe enrichi en bas de storymap. Consomme `assembly.audit_chain` pour afficher contributeurs, nombre de traitements, nombre de cartes, version.

Référence Blancarde : `CEREMA DTerMed · DTVB/AU · S. Michelon -> X. Durang -> N. Laval · Mars 2026` + footer `36 traitements · 9 cartes`.

### 5.1 Pydantic

```python
# Dans Component.params pour kind=pipeline_signature :
# {
#   "contributors_override": ["S. Michelon", "X. Durang", "N. Laval"],  # optionnel
#   "n_treatments_override": 36,   # optionnel (sinon depuis audit_chain)
#   "n_maps_override": 9,          # optionnel
#   "organization": "CEREMA DTerMed",
#   "department": "DTVB/AU",
#   "date_label": "Mars 2026",
#   "version_label": "v12 DSFR"    # optionnel
# }
```

### 5.2 Template partial `_pipeline_signature_partial.j2`

```jinja2
{# Partial _pipeline_signature — footer signe enrichi
   Vague E1. Consomme audit_chain metadata.

   Variables :
   - contributors : list[str]
   - n_treatments : int
   - n_maps : int
   - organization : str
   - department : str
   - date_label : str
   - version_label : str optionnel
#}
<div style="padding:20px 0;margin-top:32px;border-top:1px solid #e5e5e5">
  <p style="font-size:12px;color:#666;margin:0;line-height:1.6">
    {{ organization | e }}
    {% if department %} · {{ department | e }}{% endif %}
    {% if contributors %} · {{ contributors | join(' &#8594; ') }}{% endif %}
    {% if date_label %} · {{ date_label | e }}{% endif %}
  </p>
  <p style="font-size:11px;color:#999;margin:6px 0 0">
    {% if version_label %}{{ version_label | e }} · {% endif %}
    {{ n_treatments }} traitement{{ 's' if n_treatments > 1 else '' }}
    · {{ n_maps }} carte{{ 's' if n_maps > 1 else '' }}
  </p>
</div>
```

### 5.3 Helper render (main.py)

```python
elif kind == "pipeline_signature":
    # Merge params avec audit_chain si disponible
    audit_ref = comp_manifest.get("_audit_chain_ref", {})
    contributors = (params.get("contributors_override")
                    or audit_ref.get("contributors", []))
    n_treatments = (params.get("n_treatments_override")
                    or audit_ref.get("n_treatments", 0))
    n_maps = (params.get("n_maps_override")
              or audit_ref.get("n_maps", 0))
    tpl = _maplibre_jinja.get_template("_pipeline_signature_partial.j2")
    return tpl.render(
        contributors=contributors,
        n_treatments=n_treatments,
        n_maps=n_maps,
        organization=params.get("organization", "CEREMA"),
        department=params.get("department", ""),
        date_label=params.get("date_label", ""),
        version_label=params.get("version_label", ""),
    )
```

### 5.4 Exemple manifest JSON

```json
{
  "id": "f1a2b3c4d5e6",
  "version": 1,
  "kind": "pipeline_signature",
  "title": "Signature",
  "source": {"scope": "project", "sid": "abcdef012345", "pid": "fedcba543210"},
  "params": {
    "organization": "CEREMA DTerMed",
    "department": "DTVB/AU",
    "contributors_override": ["S. Michelon", "X. Durang", "N. Laval"],
    "n_treatments_override": 36,
    "n_maps_override": 9,
    "date_label": "Mars 2026",
    "version_label": "v12 DSFR"
  },
  "rendering": {"runtime": "html", "container_size": "responsive", "theme": "dsfr"}
}
```

---

## 6. Extension schema AuditChain

**Fichier** : `hub/hub/models/audit_chain.py`
**Contrainte** : tous les champs optionnels avec defaults vides (backward-compat).

### 6.1 Nouveaux modeles Pydantic

```python
# ── Vague E1 extensions ──────────────────────────────────────────────────────


class Phase(BaseModel):
    """Phase nommee du pipeline (ex: 'base', 'diagnostic', 'graphe').

    Permet de grouper les tool_calls_made par phase logique pour
    l'audit_chain_narrative auto-generation.
    """
    code: str = Field(..., description="Code court (ex: 'T01-T10', 'base')")
    name: str = Field(..., description="Nom affiche (ex: 'Base cartographique')")
    tool_ids: list[str] = Field(
        default_factory=list,
        description="IDs des tool_calls appartenant a cette phase"
    )


class VariableReliability(BaseModel):
    """Niveau de fiabilite d'une variable produite.

    Consomme par reliability_matrix pour rendu visuel, et par
    pipeline_signature pour le comptage global.
    """
    variable: str = Field(..., description="Nom variable (ex: 'Voirie', 'Canyon')")
    level: Literal["HAUTE", "MOYENNE", "BASSE"] = "MOYENNE"
    reason: str | None = Field(
        None, description="Justification (ex: 'Source officielle IGN v3')"
    )
```

### 6.2 Champs ajoutes a AuditChain

```python
class AuditChain(BaseModel):
    # ... existants inchanges ...

    # ── Vague E1 (backward-compat optionnels) ─────────────────────────────
    pipeline_phases: list[Phase] = Field(
        default_factory=list,
        description=(
            "Phases logiques du pipeline (ex: Base T01-T10, Diagnostic T11-T14). "
            "Permet auto-generation chain_groups pour audit_chain_narrative."
        ),
    )
    reliability: list[VariableReliability] = Field(
        default_factory=list,
        description="Fiabilite par variable — consomme par reliability_matrix",
    )
    contributors: list[str] = Field(
        default_factory=list,
        description="Noms contributeurs (ex: ['S. Michelon', 'X. Durang', 'N. Laval'])",
    )
    n_treatments: int = Field(
        0, ge=0,
        description="Nombre de traitements dans le pipeline (ex: 36)",
    )
    n_maps: int = Field(
        0, ge=0,
        description="Nombre de cartes produites (ex: 9)",
    )
```

### 6.3 Impact sur integrity_hash

Aucun impact breaking : `canonical_dict()` utilise `model_dump(mode="json")` qui inclut automatiquement les nouveaux champs. Les assemblages existants (sans ces champs) auront les defaults vides, donc le hash ne change pas.

---

## 7. Extension narrative_text KPI inline

**Rôle** : Permettre d'inserer des KPI inline dans les paragraphes markdown via pattern `{{ kpi:VALUE | LABEL }}`.

Référence Blancarde : `35 voies impasses voiture` avec "35" en gros bleu et "voies impasses voiture" en petit gris.

### 7.1 Pattern markdown

```
{{ kpi:35 | voies impasses voiture }}
{{ kpi:97/100 | convertibles sans impact }}
```

Rendu HTML :
```html
<span style="font-size:20px;font-weight:700;color:#000091">35</span>
<span style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:.3px;margin-left:4px">voies impasses voiture</span>
```

### 7.2 Modification `_markdown_to_html_basique` (main.py)

```python
def _markdown_to_html_basique(md: str) -> str:
    """Convertit markdown simple en HTML (H1-H3 + paragraphes + KPI inline).

    Vague E1 : pattern {{ kpi:VALUE | LABEL }} pour KPI inline DSFR.
    Backward-compat : markdown sans pattern continue de fonctionner.
    """
    import html as _h
    import re as _re

    def _replace_kpi_inline(text: str) -> str:
        """Remplace {{ kpi:VALUE | LABEL }} par spans DSFR inline."""
        def _kpi_match(m):
            value = _h.escape(m.group(1).strip())
            label = _h.escape(m.group(2).strip())
            return (
                f'<span style="font-size:20px;font-weight:700;color:#000091">{value}</span>'
                f'<span style="font-size:11px;color:#666;text-transform:uppercase;'
                f'letter-spacing:.3px;margin-left:4px">{label}</span>'
            )
        return _re.sub(r'\{\{\s*kpi:([^|]+)\|([^}]+)\}\}', _kpi_match, text)

    lines = (md or "").split("\n")
    rendered = []
    in_para: list[str] = []

    def _flush_para():
        if in_para:
            text = _h.escape(" ".join(in_para))
            text = _replace_kpi_inline(text)  # KPI inline apres escape
            rendered.append(f'<p>{text}</p>')
            in_para.clear()

    for ln in lines:
        s = ln.strip()
        if s.startswith("### "):
            _flush_para()
            rendered.append(f"<h3>{_h.escape(s[4:])}</h3>")
        elif s.startswith("## "):
            _flush_para()
            rendered.append(f"<h2 style='color:#000091'>{_h.escape(s[3:])}</h2>")
        elif s.startswith("# "):
            _flush_para()
            rendered.append(f"<h1 style='color:#000091'>{_h.escape(s[2:])}</h1>")
        elif not s:
            _flush_para()
        else:
            in_para.append(s)
    _flush_para()
    return "".join(rendered)
```

**Attention** : Le regex doit tourner APRES `html.escape()` car les `{{` et `}}` sont echappes en `&#123;&#123;` etc. Il faut donc appliquer le regex sur le texte brut AVANT escape, ou adapter le regex. Solution recommandee :

```python
def _flush_para():
    if in_para:
        raw = " ".join(in_para)
        # Extraire KPI patterns AVANT html.escape
        kpi_tokens = {}
        def _stash_kpi(m):
            key = f"__KPI_{len(kpi_tokens)}__"
            value = _h.escape(m.group(1).strip())
            label = _h.escape(m.group(2).strip())
            kpi_tokens[key] = (
                f'<span style="font-size:20px;font-weight:700;color:#000091">{value}</span>'
                f'<span style="font-size:11px;color:#666;text-transform:uppercase;'
                f'letter-spacing:.3px;margin-left:4px">{label}</span>'
            )
            return key
        raw = _re.sub(r'\{\{\s*kpi:([^|]+)\|([^}]+)\}\}', _stash_kpi, raw)
        text = _h.escape(raw)
        for key, html_val in kpi_tokens.items():
            text = text.replace(key, html_val)
        rendered.append(f'<p>{text}</p>')
        in_para.clear()
```

### 7.3 Exemple usage markdown

```markdown
## Circulation

Comment circule-t-on dans le quartier ? L'analyse du reseau viaire revele
{{ kpi:35 | voies impasses voiture }} avec passage pieton en bout.

La centralite montre que {{ kpi:212 | ponts critiques }} concentrent
l'essentiel des flux automobiles.
```

Rendu attendu : paragraphes normaux avec les valeurs 35 et 212 en gros bleu inline.

---

## Recapitulatif des modifications par fichier

| Fichier | Modifications |
|---------|---------------|
| `hub/hub/models/component.py` | Ajout 5 kinds dans `ComponentKind` Literal : `kpi_grid`, `audit_chain_narrative`, `chapter_section`, `reliability_matrix`, `pipeline_signature` |
| `hub/hub/models/audit_chain.py` | Ajout `Phase`, `VariableReliability` models + 5 champs optionnels sur `AuditChain` |
| `hub/hub/maplibre_renderer/` | 5 nouveaux partials `.j2` |
| `hub/hub/main.py` | 5 branches `elif kind ==` dans `_pre_render_component_html` + modification `_markdown_to_html_basique` pour KPI inline + signature etendue avec `rendered_components` param |
| `hub/hub/main.py` | `ComponentRendering.runtime` : pas de nouveau runtime (tous en `"html"`) |

## Convention de nommage

Tous les nouveaux kinds suivent le pattern Vague A :
- Model Pydantic : pas de model dedie kind-specific (params restent `dict[str, Any]`)
- Template : `_{kind}_partial.j2` (sans `<head>`/`<body>`, embeddable)
- Helper : branche `elif kind ==` dans `_pre_render_component_html`
- Runtime : `"html"` pour tous (pas de dependance JS externe)

## Notes d'implementation

1. **Ordre de rendu** : `chapter_section` reference d'autres composants par `ref` — il faut les rendre AVANT le chapter_section. L'ordre actuel dans `_render_assembly_html` (boucle sections -> components) doit etre adapte : premiere passe = rendre tous les composants non-chapter, deuxieme passe = rendre les chapter_section avec le dict `rendered_components`.

2. **audit_chain_narrative + pipeline_signature** : ces deux kinds consomment `audit_chain` de l'assembly. Le helper doit recevoir une ref vers l'audit_chain courant via `comp_manifest["_audit_chain_ref"]` (injecte par `_render_assembly_html` avant le pre-rendu).

3. **fr-tabs JS** : le DSFR JS (`dsfr.min.js`) doit etre charge dans le `<head>` du template parent (storymap_dsfr.html.j2) pour que les onglets fonctionnent. Si absent, fallback : tous les panels sont visibles (pas de tabs interactifs, mais contenu accessible).

4. **Backward-compat absolue** : aucun champ existant n'est modifie. Tous les nouveaux champs ont des defaults vides. Un assembly Vague A continue de se serialiser et se rendre identiquement.
