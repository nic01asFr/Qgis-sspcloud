"""
hub.storymap_dsfr — Builder DSFR pour storymaps cartographiques CEREMA.

Pattern extrait de trois exemples de référence :
  - stationnement_4eme.html        (hero custom + KPI cards + Leaflet)
  - observatoire_solaire_dsfr.html (DSFR complet + canvas 3D + sidebar)
  - storymap_blancarde_chartreux   (DSFR scrollytelling + #methodo + SVG)

Principes invariants :
  1. Header DSFR (République Française + operator CEREMA)
  2. fr-notice (statut de l'étude)
  3. Chapitres alternant fond gris/blanc (background-alt-grey)
  4. .chapter-title bleu france avec border-bottom 3px
  5. Section #methodo avec chain-badges : 📥 data → ⚙️ algo → 📊 result
     (c'est LE pattern d'explicabilité affichée illustrée et claire)
  6. Section #tracabilite avec fr-accordion (sources / pipeline / fiabilité)
  7. Footer DSFR

Usage :
    from hub.storymap_dsfr import StorymapBuilder

    sb = StorymapBuilder(
        title="Inondations — Le Lavandou",
        subtitle="Risque T100 sur les bâtiments exposés",
        operator="CEREMA",
        operator_sub="DTerMed · DTVB/AU",
        notice="Étude exploratoire — données expérimentales (mai 2026)",
    )
    sb.add_kpis([
        {"value": "1 248", "unit": "bâtiments", "label": "Exposés T100", "color": "red"},
        {"value": "32",    "unit": "ha",       "label": "Surface inondée", "color": "blue"},
    ])
    sb.add_chapter(
        title="Chapitre 1 — Zones exposées",
        intro="Le scénario centennal touche le centre-ville historique...",
        blocks=[
            {"kind": "callout", "text": "<strong>32 ha</strong> de bâti exposé."},
            {"kind": "map_leaflet", "id": "m1", "center": [43.13, 6.36], "zoom": 14,
             "geojson_url": "https://.../zones.geojson"},
        ],
    )
    sb.add_methodology(
        intro="Comment cette carte est construite",
        steps=[
            {"n": 1, "title": "Bâti exposé",
             "desc": "Croisement bâti BD TOPO × emprise inondable T100",
             "data_in": "BD TOPO + TRI",
             "algo": "QGIS native:intersection",
             "result": "1 248 polygones"},
        ],
    )
    sb.add_traceability(
        sources=["BD TOPO v3 (IGN)", "TRI inondation T100 (DGPR)"],
        pipeline=["T01 — Téléchargement WFS",
                  "T02 — Intersection bâti × emprise",
                  "T03 — Symbologie YlOrRd"],
        fiability="Précision de l'emprise : ±5m (donnée DGPR).",
    )
    html = sb.render()
    Path("/data/exports/inondations_lavandou.html").write_text(html, encoding="utf-8")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_DSFR_VERSION = "1.12.1"
_LEAFLET_VERSION = "1.9.4"


# ── Styles communs (en plus de DSFR) ──────────────────────────────────────────

_BASE_STYLES = r"""
.chapter-title{font-size:18px;font-weight:700;color:var(--text-title-blue-france);
  border-bottom:3px solid var(--border-action-high-blue-france);
  padding-bottom:8px;margin-bottom:16px}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:12px;margin:16px 0}
.kpi-card{background:#fff;border-radius:4px;padding:14px 16px;
  border-top:4px solid var(--border-action-high-blue-france);
  box-shadow:0 1px 4px rgba(0,0,0,.08)}
.kpi-card--red{border-top-color:#c1351a}
.kpi-card--green{border-top-color:#1f6f3f}
.kpi-card--orange{border-top-color:#d64d00}
.kpi-value{font-size:1.8rem;font-weight:700;color:var(--text-title-grey);
  line-height:1;margin-bottom:.35rem}
.kpi-unit{font-size:.95rem;font-weight:400;color:#666;margin-left:4px}
.kpi-label{font-size:.85rem;color:#666;margin-top:.25rem}
.chain-badges{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.chain-badges .fr-badge{font-size:.7rem}
.method-step{background:#fff;border-radius:4px;padding:14px 18px;
  border-left:4px solid var(--border-action-high-blue-france);
  box-shadow:0 1px 3px rgba(0,0,0,.06);margin-bottom:12px}
.method-step h3{font-size:1rem;font-weight:700;color:var(--text-title-blue-france);
  margin:0 0 6px}
.method-step p{font-size:.9rem;color:#555;margin:0 0 4px;line-height:1.5}
.method-step .svg-explain{margin-top:10px;background:#fafafe;padding:10px;
  border-radius:3px;border:1px solid #eee}
.map-frame{height:520px;border-radius:4px;box-shadow:0 1px 6px rgba(0,0,0,.12);
  margin:8px 0;background:#eef0f5}
.map-caption{font-size:.78rem;color:#888;margin-top:.4rem;font-style:italic}
.sources-list{font-size:.85rem;line-height:1.7}
.sources-list li{margin-bottom:4px}
@media(max-width:700px){.kpi-grid{grid-template-columns:1fr 1fr}}
"""


# ── Bloc-builders (HTML fragments) ────────────────────────────────────────────

def _kpi_card(kpi: dict[str, Any]) -> str:
    color = kpi.get("color", "blue")
    cls = {
        "red": "kpi-card kpi-card--red",
        "green": "kpi-card kpi-card--green",
        "orange": "kpi-card kpi-card--orange",
        "blue": "kpi-card",
    }.get(color, "kpi-card")
    unit = f'<span class="kpi-unit">{kpi["unit"]}</span>' if kpi.get("unit") else ""
    return (
        f'<div class="{cls}">'
        f'<div class="kpi-value">{kpi["value"]} {unit}</div>'
        f'<div class="kpi-label">{kpi.get("label","")}</div>'
        f'</div>'
    )


def _callout(text: str, kind: str = "info") -> str:
    return (
        f'<div class="fr-callout fr-mt-2w">'
        f'<p class="fr-callout__text">{text}</p>'
        f'</div>'
    )


def _map_leaflet(block: dict[str, Any], idx: int) -> tuple[str, str]:
    """Renvoie (html, js) pour une carte Leaflet."""
    mid = block.get("id") or f"map{idx}"
    center = block.get("center", [46.5, 2.5])
    zoom = block.get("zoom", 13)
    geojson_url = block.get("geojson_url", "")
    style_js = block.get("style_js", "{color:'#000091',weight:2,fillOpacity:.4}")
    popup_field = block.get("popup_field")
    caption = block.get("caption", "Cliquer une entité pour ses attributs.")

    html = (
        f'<div id="{mid}" class="map-frame"></div>'
        f'<p class="map-caption">{caption}</p>'
    )

    popup_js = ""
    if popup_field:
        popup_js = (
            f"onEachFeature:(f,l)=>{{if(f.properties&&f.properties['{popup_field}'])"
            f"l.bindPopup(String(f.properties['{popup_field}']));}}"
        )

    if geojson_url:
        js = f"""
(function(){{
  const m = L.map('{mid}').setView({json.dumps(center)}, {zoom});
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_nolabels/{{z}}/{{x}}/{{y}}.png',
    {{attribution:'© OpenStreetMap, © CartoDB',maxZoom:19}}).addTo(m);
  fetch('{geojson_url}').then(r=>r.json()).then(gj=>{{
    const layer = L.geoJSON(gj,{{style:()=>({style_js}){"," + popup_js if popup_js else ""}}}).addTo(m);
    try{{m.fitBounds(layer.getBounds(),{{padding:[20,20]}});}}catch(e){{}}
  }});
}})();
"""
    else:
        js = f"""
(function(){{
  const m = L.map('{mid}').setView({json.dumps(center)}, {zoom});
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_nolabels/{{z}}/{{x}}/{{y}}.png',
    {{attribution:'© OpenStreetMap, © CartoDB',maxZoom:19}}).addTo(m);
}})();
"""
    return html, js


def _image(block: dict[str, Any]) -> str:
    src = block["src"]
    alt = block.get("alt", "")
    caption = block.get("caption", "")
    cap_html = f'<p class="map-caption">{caption}</p>' if caption else ""
    return (
        f'<figure class="fr-content-media">'
        f'<img src="{src}" alt="{alt}" '
        f'style="width:100%;border-radius:4px;box-shadow:0 1px 6px rgba(0,0,0,.12)">'
        f'{cap_html}</figure>'
    )


def _html_block(block: dict[str, Any]) -> str:
    """Bloc HTML libre (l'agent peut injecter ses propres composants)."""
    return block.get("html", "")


def _method_step(step: dict[str, Any]) -> str:
    """
    Un step de méthodologie avec chain-badges (pattern clé d'explicabilité).
    📥 data_in → ⚙️ algo → 📊 result
    """
    n = step.get("n", "")
    title = step.get("title", "")
    desc = step.get("desc", "")
    data_in = step.get("data_in", "")
    algo = step.get("algo", "")
    result = step.get("result", "")
    svg = step.get("svg_explain", "")  # SVG inline optionnel

    badges = ""
    if data_in or algo or result:
        badges = '<div class="chain-badges">'
        if data_in:
            badges += (
                f'<span class="fr-badge fr-badge--info fr-badge--sm fr-badge--no-icon">'
                f'📥 {data_in}</span>'
            )
        if algo:
            badges += (
                f'<span class="fr-badge fr-badge--new fr-badge--sm fr-badge--no-icon">'
                f'⚙️ {algo}</span>'
            )
        if result:
            badges += (
                f'<span class="fr-badge fr-badge--success fr-badge--sm fr-badge--no-icon">'
                f'📊 {result}</span>'
            )
        badges += '</div>'

    svg_html = f'<div class="svg-explain">{svg}</div>' if svg else ""

    return (
        f'<div class="method-step">'
        f'<h3>{n} · {title}</h3>'
        f'<p>{desc}</p>'
        f'{badges}'
        f'{svg_html}'
        f'</div>'
    )


# ── Builder principal ─────────────────────────────────────────────────────────

@dataclass
class StorymapBuilder:
    title: str
    subtitle: str = ""
    operator: str = "CEREMA"
    operator_sub: str = ""
    service_title: str | None = None
    notice: str = ""
    lang: str = "fr"
    extra_css: str = ""
    extra_head: str = ""

    _kpis: list[dict] = field(default_factory=list)
    _chapters: list[dict] = field(default_factory=list)
    _methodology: dict | None = None
    _traceability: dict | None = None
    _js_inits: list[str] = field(default_factory=list)
    _needs_leaflet: bool = False

    # ── API publique ──────────────────────────────────────────────────────

    def add_kpis(self, kpis: list[dict[str, Any]]) -> "StorymapBuilder":
        """KPI cards en haut (sous le hero)."""
        self._kpis.extend(kpis)
        return self

    def add_chapter(
        self,
        title: str,
        intro: str = "",
        blocks: list[dict[str, Any]] | None = None,
        bg_alt: bool | None = None,
    ) -> "StorymapBuilder":
        """
        Un chapitre = une section avec chapter-title + contenu.
        bg_alt=True force fond gris ; None alterne automatiquement.
        Types de blocs supportés :
          {"kind": "callout", "text": "..."}
          {"kind": "map_leaflet", "id": "m1", "center": [lat,lon], "zoom": 14,
                                  "geojson_url": "...", "popup_field": "nom"}
          {"kind": "image", "src": "...", "caption": "..."}
          {"kind": "html", "html": "<div>...</div>"}
        """
        self._chapters.append({
            "title": title,
            "intro": intro,
            "blocks": blocks or [],
            "bg_alt": bg_alt,
        })
        return self

    def add_methodology(
        self,
        intro: str,
        steps: list[dict[str, Any]],
    ) -> "StorymapBuilder":
        """
        Section #methodo — explicabilité affichée illustrée et claire.
        Chaque step : {"n": 1, "title": "...", "desc": "...",
                       "data_in": "📥 ...", "algo": "⚙️ ...", "result": "📊 ...",
                       "svg_explain": "<svg>...</svg>"}  # SVG optionnel
        """
        self._methodology = {"intro": intro, "steps": steps}
        return self

    def add_methodology_from_treatments(
        self,
        events: list[dict[str, Any]],
        intro: str = "Cette carte est issue des traitements suivants, "
                     "horodatés et tracés automatiquement pendant la session.",
        max_steps: int = 8,
    ) -> "StorymapBuilder":
        """
        Génère la méthodologie À PARTIR DU LOG D'AUDIT RÉEL de la session
        (treatments.jsonl produit par hub.audit_trail).

        C'est la méthode à utiliser dans les livrables CEREMA : elle garantit
        que les chain-badges affichés correspondent à des exécutions vraies.

        Usage :
          import requests
          r = requests.get(f"{HUB_URL}/sessions/{SID}/treatments",
                           headers={"Authorization": f"Bearer {KEY}"},
                           params={"kinds": "processing,export"})
          sb.add_methodology_from_treatments(r.json()["events"])
        """
        keep = {"processing", "export"}
        sig_events = [e for e in events if e.get("kind") in keep and e.get("ok")]

        # Dédup runs identiques consécutifs
        deduped, last_sig = [], None
        for e in sig_events:
            sig = (e.get("tool"), tuple(e.get("outputs") or []))
            if sig == last_sig:
                continue
            deduped.append(e)
            last_sig = sig

        steps = []
        for n, evt in enumerate(deduped[-max_steps:], start=1):
            tool = evt.get("tool", "?")
            if evt["kind"] == "processing":
                algo_short = tool.split(":")[-1] if ":" in tool else tool
                params = evt.get("params") or {}
                data_in_parts = []
                if isinstance(params, dict):
                    for k in ("INPUT", "OVERLAY", "LAYERS", "INPUT_RASTER"):
                        if k in params:
                            v = params[k]
                            if isinstance(v, str):
                                data_in_parts.append(
                                    v.rsplit("/", 1)[-1].split(".")[0]
                                )
                data_in = ", ".join(data_in_parts[:3]) or "couches du projet"
                n_out = evt.get("n_features_out")
                result_str = f"{n_out} entités" if n_out is not None else "OK"
                steps.append({
                    "n":       n,
                    "title":   algo_short.replace("_", " ").title(),
                    "desc":    evt.get("summary") or tool,
                    "data_in": data_in,
                    "algo":    tool,
                    "result":  result_str,
                })
            else:  # export
                params = evt.get("params") or {}
                steps.append({
                    "n":       n,
                    "title":   "Export",
                    "desc":    evt.get("summary") or "Export de fichier",
                    "data_in": "couche projet",
                    "algo":    params.get("ext", "fichier"),
                    "result":  (evt.get("outputs") or ["fichier"])[0],
                })

        self._methodology = {"intro": intro, "steps": steps}
        return self

    def add_traceability(
        self,
        sources: list[str],
        pipeline: list[str] | None = None,
        fiability: str | None = None,
        license: str = "Licence ouverte 2.0",
    ) -> "StorymapBuilder":
        """Section #tracabilite — accordions sources / pipeline / fiabilité."""
        self._traceability = {
            "sources": sources,
            "pipeline": pipeline or [],
            "fiability": fiability,
            "license": license,
        }
        return self

    def render(self) -> str:
        """Génère le HTML complet auto-suffisant."""
        # Détecter si Leaflet est requis
        for ch in self._chapters:
            for b in ch["blocks"]:
                if b.get("kind") == "map_leaflet":
                    self._needs_leaflet = True
                    break

        head = self._render_head()
        body = (
            self._render_header()
            + self._render_notice()
            + '<main role="main">'
            + self._render_hero()
            + self._render_chapters()
            + self._render_methodology()
            + self._render_traceability()
            + '</main>'
            + self._render_footer()
            + self._render_scripts()
        )

        return f'<!DOCTYPE html>\n<html lang="{self.lang}" data-fr-scheme="light">\n{head}\n<body>\n{body}\n</body>\n</html>\n'

    # ── Rendu interne ─────────────────────────────────────────────────────

    def _render_head(self) -> str:
        leaflet = (
            f'<link rel="stylesheet" '
            f'href="https://unpkg.com/leaflet@{_LEAFLET_VERSION}/dist/leaflet.css">'
            if self._needs_leaflet else ""
        )
        return f"""<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,shrink-to-fit=no">
<meta name="theme-color" content="#000091">
<title>{self.title} · {self.operator}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@gouvfr/dsfr@{_DSFR_VERSION}/dist/dsfr.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@gouvfr/dsfr@{_DSFR_VERSION}/dist/utility/utility.min.css">
{leaflet}
<style>{_BASE_STYLES}{self.extra_css}</style>
{self.extra_head}
</head>"""

    def _render_header(self) -> str:
        svc_title = self.service_title or self.title
        return f"""<header role="banner" class="fr-header">
<div class="fr-header__body"><div class="fr-container"><div class="fr-header__body-row">
  <div class="fr-header__brand fr-enlarge-link"><div class="fr-header__brand-top">
    <div class="fr-header__logo"><p class="fr-logo">République<br>Française</p></div>
    <div class="fr-header__operator">
      <p style="font-weight:700;font-size:15px;color:#000091">{self.operator}</p>
      <p style="font-size:11px;color:#666">{self.operator_sub}</p>
    </div>
  </div>
  <div class="fr-header__service"><a href="#"><p class="fr-header__service-title">{svc_title}</p></a>
    <p class="fr-header__service-tagline">{self.subtitle}</p>
  </div></div>
</div></div></div>
</header>"""

    def _render_notice(self) -> str:
        if not self.notice:
            return ""
        return f"""<div class="fr-notice fr-notice--info"><div class="fr-container"><div class="fr-notice__body">
<p class="fr-notice__title">{self.notice}</p>
</div></div></div>"""

    def _render_hero(self) -> str:
        kpi_html = ""
        if self._kpis:
            kpi_html = (
                '<div class="kpi-grid">'
                + "".join(_kpi_card(k) for k in self._kpis)
                + '</div>'
            )
        return f"""<section id="s0" class="fr-py-6w">
<div class="fr-container">
  <h1 class="fr-mb-1w">{self.title}</h1>
  <p class="fr-text--lead">{self.subtitle}</p>
  {kpi_html}
</div>
</section>"""

    def _render_chapters(self) -> str:
        out = []
        for i, ch in enumerate(self._chapters):
            bg_alt = ch["bg_alt"]
            if bg_alt is None:
                bg_alt = (i % 2 == 0)  # alterne en partant du gris
            bg = ' style="background:var(--background-alt-grey)"' if bg_alt else ""
            intro = (
                f'<p class="fr-text--sm fr-mb-3w">{ch["intro"]}</p>'
                if ch["intro"] else ""
            )
            blocks_html = []
            for j, b in enumerate(ch["blocks"]):
                kind = b.get("kind", "html")
                if kind == "callout":
                    blocks_html.append(_callout(b.get("text", "")))
                elif kind == "map_leaflet":
                    html, js = _map_leaflet(b, j)
                    blocks_html.append(html)
                    self._js_inits.append(js)
                elif kind == "image":
                    blocks_html.append(_image(b))
                elif kind == "html":
                    blocks_html.append(_html_block(b))

            out.append(f"""<section id="ch{i+1}" class="fr-py-6w"{bg}>
<div class="fr-container">
  <p class="chapter-title">{ch["title"]}</p>
  {intro}
  {"".join(blocks_html)}
</div>
</section>""")
        return "\n".join(out)

    def _render_methodology(self) -> str:
        if not self._methodology:
            return ""
        m = self._methodology
        steps_html = "".join(_method_step(s) for s in m["steps"])
        return f"""<section id="methodo" class="fr-py-6w" style="background:var(--background-alt-grey)">
<div class="fr-container" style="max-width:900px">
  <p class="chapter-title">Comment cette carte est construite</p>
  <p class="fr-text--sm fr-mb-3w">{m["intro"]}</p>
  {steps_html}
</div>
</section>"""

    def _render_traceability(self) -> str:
        if not self._traceability:
            return ""
        t = self._traceability
        sources_html = (
            '<ul class="sources-list fr-mt-2w fr-text--sm">'
            + "".join(f'<li>{s}</li>' for s in t["sources"])
            + '</ul>'
        )
        pipeline_html = ""
        if t["pipeline"]:
            pipeline_html = f"""<section class="fr-accordion">
<h3 class="fr-accordion__title">
<button class="fr-accordion__btn" aria-expanded="false" aria-controls="acc-pipe">Pipeline de traitement ({len(t["pipeline"])} étapes)</button>
</h3>
<div class="fr-collapse" id="acc-pipe">
<ol class="sources-list fr-text--sm">
{"".join(f'<li>{p}</li>' for p in t["pipeline"])}
</ol>
</div>
</section>"""

        fiability_html = ""
        if t["fiability"]:
            fiability_html = f"""<section class="fr-accordion">
<h3 class="fr-accordion__title">
<button class="fr-accordion__btn" aria-expanded="false" aria-controls="acc-fiab">Fiabilité &amp; limites</button>
</h3>
<div class="fr-collapse" id="acc-fiab">
<p class="fr-text--sm">{t["fiability"]}</p>
</div>
</section>"""

        return f"""<section id="tracabilite" class="fr-py-6w">
<div class="fr-container" style="max-width:900px">
  <p class="chapter-title">Traçabilité</p>
  <p class="fr-text--sm">Sources utilisées pour produire cette carte :</p>
  {sources_html}
  {pipeline_html}
  {fiability_html}
</div>
</section>"""

    def _render_footer(self) -> str:
        license = (self._traceability or {}).get("license", "Licence ouverte 2.0")
        return f"""<footer class="fr-footer" role="contentinfo"><div class="fr-container">
<div class="fr-footer__body">
  <div class="fr-footer__brand fr-enlarge-link">
    <p class="fr-logo">République<br>Française</p>
  </div>
  <div class="fr-footer__content">
    <p class="fr-footer__content-desc">{self.operator} {self.operator_sub} — {self.title}</p>
    <ul class="fr-footer__content-list">
      <li class="fr-footer__content-item"><a class="fr-footer__content-link" href="https://cerema.fr">cerema.fr</a></li>
      <li class="fr-footer__content-item"><a class="fr-footer__content-link" href="https://geoservices.ign.fr">geoservices.ign.fr</a></li>
    </ul>
  </div>
</div>
<div class="fr-footer__bottom"><ul class="fr-footer__bottom-list">
  <li class="fr-footer__bottom-item"><span class="fr-footer__bottom-link">{license}</span></li>
</ul></div>
</div></footer>"""

    def _render_scripts(self) -> str:
        dsfr_js = (
            f'<script type="module" '
            f'src="https://cdn.jsdelivr.net/npm/@gouvfr/dsfr@{_DSFR_VERSION}/dist/dsfr.module.min.js">'
            f'</script>'
        )
        leaflet_js = (
            f'<script src="https://unpkg.com/leaflet@{_LEAFLET_VERSION}/dist/leaflet.js"></script>'
            if self._needs_leaflet else ""
        )
        inits = (
            f'<script>\n{"".join(self._js_inits)}\n</script>'
            if self._js_inits else ""
        )
        return f"{dsfr_js}\n{leaflet_js}\n{inits}"


# ── Helpers de plus haut niveau ───────────────────────────────────────────────

def quick_storymap(
    title: str,
    subtitle: str,
    intro: str,
    map_geojson_url: str,
    map_center: list[float],
    sources: list[str],
    kpis: list[dict] | None = None,
    operator_sub: str = "DTerMed",
) -> str:
    """Storymap minimale en une seule fonction — pour usage rapide de l'agent."""
    sb = StorymapBuilder(
        title=title,
        subtitle=subtitle,
        operator_sub=operator_sub,
        notice="Étude exploratoire — données expérimentales",
    )
    if kpis:
        sb.add_kpis(kpis)
    sb.add_chapter(
        title="Vue d'ensemble",
        intro=intro,
        blocks=[
            {"kind": "map_leaflet", "id": "m1",
             "center": map_center, "zoom": 14,
             "geojson_url": map_geojson_url},
        ],
        bg_alt=False,
    )
    sb.add_traceability(sources=sources)
    return sb.render()
