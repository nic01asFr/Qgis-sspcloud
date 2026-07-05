"""
hub.publish.pdf - F15 Export PDF headless via weasyprint.

Sprint 1.4 Vague 1 Equipe B (2026-07-05).

Choix produit : **weasyprint** > pyppeteer / playwright.
Motifs :
- Leger (pas de Chromium 500Mo embarque)
- Print CSS media dedie (@page A4, page-break-inside: avoid)
- Rendu deterministe (pas de flakiness JS timeout)
- Deja utilise cote CEREMA pour livrables PDF
- Ne rend PAS le JavaScript : MapLibre WebGL fallback -> tuile raster
  (v1 : placeholder ; v2 : screenshot serveur via mapserver render).

Dep systeme sur Linux : `libpango-1.0-0 libpangoft2-1.0-0 libcairo2`
(pod hub Docker image doit les inclure).

L'integrity_hash est injecte en pied de page (@page bottom-center) pour
auditabilite.
"""

from __future__ import annotations

import io
import logging
import re

log = logging.getLogger("hub.publish.pdf")


_PRINT_CSS = """
/* F15 Print CSS - Sprint 1.4 Vague 1 Equipe B */
@page {
  size: A4 portrait;
  margin: 20mm 15mm 25mm 15mm;
  @bottom-center {
    content: "QGIS-SSPCloud - " string(integrity-hash) " - page " counter(page) " / " counter(pages);
    font-size: 8pt;
    color: #666;
    font-family: sans-serif;
  }
  @top-right {
    content: "CEREMA";
    font-size: 8pt;
    color: #000091;
    font-weight: 700;
  }
}
body { font-family: sans-serif; color: #161616; }
h1, h2, h3 { color: #000091; page-break-after: avoid; }
.story-section, .story-component, aside, header { page-break-inside: avoid; }
.story-component--map, .story-component--full,
.story-component-iframe, iframe {
  height: 200px !important;
  background: repeating-linear-gradient(45deg, #f0f3f9, #f0f3f9 10px, #e0e6f2 10px, #e0e6f2 20px);
  border: 1px solid #000091;
  position: relative;
}
.story-component--map::before, .story-component--full::before {
  content: "Carte interactive - consulter la version HTML/ZIP pour interaction";
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: #fff;
  padding: 8px 14px;
  border-radius: 4px;
  border: 1px solid #000091;
  color: #000091;
  font-size: 11pt;
  font-weight: 600;
  text-align: center;
}
.publication-obsolete-banner { page-break-inside: avoid; }
h1 { string-set: chapter content(); }
"""


def _inject_integrity_string_set(html: str, integrity_hash: str) -> str:
    css_str_set = (
        f'<style>body::before {{ content: ""; string-set: integrity-hash "{integrity_hash}"; }}</style>'
    )
    if "<head>" in html:
        return html.replace("<head>", "<head>\n" + css_str_set, 1)
    return css_str_set + html


def _inject_print_css(html: str) -> str:
    css_block = f"<style media=\"print\">{_PRINT_CSS}</style>"
    if "</head>" in html:
        return html.replace("</head>", css_block + "</head>", 1)
    return css_block + html


def _neutralize_scripts(html: str) -> str:
    """Retire les balises <script> qui peuvent bloquer/casser weasyprint."""
    return re.sub(
        r"<script[^>]*>.*?</script>", "", html,
        flags=re.DOTALL | re.IGNORECASE,
    )


def render_pdf_from_html(
    html: str,
    integrity_hash: str = "sha256:unknown",
    base_url: str | None = None,
) -> bytes:
    """Convertit HTML -> PDF via weasyprint.

    - Injecte Print CSS media dedie (A4, page-break-inside: avoid)
    - Retire les balises <script>
    - Injecte integrity_hash en footer via CSS string-set + badge HTML

    Peut lever ImportError si weasyprint indisponible (dev Windows sans
    Pango). Utiliser pytest.importorskip("weasyprint") cote tests.
    """
    from weasyprint import HTML  # noqa: PLC0415

    processed = _inject_print_css(html)
    processed = _inject_integrity_string_set(processed, integrity_hash)
    processed = _neutralize_scripts(processed)

    if "</body>" in processed:
        badge = (
            f'<div style="font-size:8pt;color:#666;text-align:center;'
            f'padding:10px;border-top:1px solid #ccc;margin-top:20px">'
            f'Integrity hash : {integrity_hash}</div>'
        )
        processed = processed.replace("</body>", badge + "</body>", 1)

    pdf_io = io.BytesIO()
    HTML(string=processed, base_url=base_url or "").write_pdf(pdf_io)
    return pdf_io.getvalue()


def render_pdf(
    sid: str, aid: str, username: str, integrity_hash: str,
    render_html_fn,
) -> bytes:
    """Wrapper : appelle render_html_fn puis convertit en PDF."""
    import asyncio
    if asyncio.iscoroutinefunction(render_html_fn):
        loop = asyncio.get_event_loop()
        html, chain = loop.run_until_complete(render_html_fn(sid, aid, username))
    else:
        html, chain = render_html_fn(sid, aid, username)
    ih = (chain.get("integrity_hash") or chain.get("signed_hash")
          or integrity_hash)
    return render_pdf_from_html(html, integrity_hash=ih)
