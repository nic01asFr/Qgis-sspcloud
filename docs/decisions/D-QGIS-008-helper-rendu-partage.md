# D-QGIS-008 — Helper rendu partagé `_pre_render_component_html`

**Date** : 2026-06-29  
**Statut** : ACTÉ  
**Auteur** : Composants-Architect  
**Validateurs** : Lead-cerema (libre arbitre interne), Passerelle-Archi (DANS_TA_LANE)

## Conclusion

Fonction `_pre_render_component_html(comp_manifest, sid, username) -> str`
devient source de vérité unique pour le rendu HTML d'un composant.

Consommée par :
- `render_component_endpoint` (path standalone iframe)
- `_render_assembly_html` (path inline storymap publication)

## Rationale

État actuel (avant Vague A) :
- Path standalone utilise templates Jinja2 (`maplibre_renderer/*.j2`)
- Path inline utilise strings hardcodés Python dans `rendered_components[cid]`

→ **Divergence latente** : un développeur peut ajouter une feature
(ex: `data_url` handling) à un path et pas l'autre → bug fantôme.

## Implémentation

Vague A commit 2.

### Pattern templates partials

```jinja2
{# _kpi_badge_partial.j2 — pré-rendu inline #}
<div class="story-component story-component--kpi">
  <div class="kpi-value">{{ params.value }}{{ params.unit | default('') }}</div>
  <div class="kpi-label">{{ params.label }}</div>
</div>
```

```jinja2
{# kpi_badge.html.j2 — template standalone existant (inchangé) #}
<!DOCTYPE html>
<html><head>...</head><body>
{% include "_kpi_badge_partial.j2" %}
</body></html>
```

### Helper Python unique

```python
async def _pre_render_component_html(
    comp_manifest: dict, sid: str, username: str,
) -> str:
    """Source unique du rendu HTML composant. Consommé par :
    - render_component_endpoint (standalone iframe)
    - _render_assembly_html (inline storymap)
    
    Templates partials `_{kind}_partial.j2` (sans head/body) garantissent
    cohérence rendu cross-path.
    """
    kind = comp_manifest["kind"]
    template_name = f"_{kind}_partial.j2"
    ctx = await _build_component_ctx(comp_manifest, sid, username)
    return _maplibre_jinja.get_template(template_name).render(**ctx)
```

## Conséquences

- Tests rétro-compat : `render_component_endpoint(cid).html` doit
  contenir `<head>...<body>` + `_pre_render_component_html(cid)`
- Pas d'impact écosystème : refactor intra qgis-sspcloud
- Si pré-rendu charge geoai-kit en CDN/script (D2 future) : URL pinned
  vers version figée (Passerelle-Archi note)

## Référence wikichat

- Canal : `#qgis-sspcloud-sprint-co`
- #decisions msg : `f8dda810` (2026-06-29)
- Passerelle-Archi verdict : `DANS_TA_LANE` (msg 6c517f58)
