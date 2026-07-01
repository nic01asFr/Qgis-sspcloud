/**
 * Design tokens CEREMA — Sprint V1.16.0 (2026-07-01).
 *
 * Extraction inline styles AgentPanel V1.15 vers tokens centralises pour :
 * - coherence cross-composants blocknote-editor
 * - futur dark mode (override via CSS vars)
 * - reusabilite cross-projet CEREMA (ZEBRA, IISR-Audit, atlas-territorial)
 *
 * Palette DSFR stricte (Design System de l'Etat, gouvernement francais) :
 * https://www.systeme-de-design.gouv.fr/
 *
 * Convention : les tokens sont des string CSS pretes a utiliser inline React
 * (style={{ color: T.textPrimary }}). Un bloc <style> global (agentPanelCss)
 * injecte les CSS vars root en fallback pour dark mode futur.
 */

export const T = {
  // === COULEURS DSFR STRICT ===
  // Bleu Marianne (identite Republique)
  blueMarianne: '#000091',
  blueMarianneHover: '#1212ff',
  blueMarianneLight: '#f5f5fe',
  blueMarianneBorder: '#ececfa',
  blueFocusRing: '#0a76f6',

  // Rouge Marianne
  redMarianne: '#e1000f',

  // Semantique DSFR officielle
  successFg: '#18753c',
  successBg: '#b8fec9',
  successBgLight: '#e3fff0',
  warningFg: '#b34000',
  warningBg: '#ffe9c6',
  errorFg: '#ce0500',
  errorBg: '#fddede',

  // Neutres
  white: '#ffffff',
  textPrimary: '#161616',
  textSecondary: '#3a3a3a',
  textMuted: '#666666',
  textDisabled: '#929292',
  borderDefault: '#dddddd',
  borderStrong: '#3a3a3a',
  bgAlt: '#f6f6f6',

  // === TYPO ===
  fontFamily:
    "'Marianne', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
  fontSizeXs: 10,
  fontSizeSm: 11,
  fontSizeBase: 12,
  fontSizeMd: 13,
  fontSizeLg: 14,
  fontWeightRegular: 400,
  fontWeightMedium: 500,
  fontWeightBold: 600,
  letterSpacingWide: 0.5,

  // === ESPACEMENTS (DSFR spacer 4px base) ===
  space1: 4,
  space2: 8,
  space3: 12,
  space4: 16,
  space5: 20,
  space6: 24,

  // === RADIUS ===
  radiusSm: 3,
  radiusMd: 4,
  radiusLg: 8,

  // === PANEL ===
  panelWidthMax: 340,
  panelWidthMin: 300,
  panelCollapsedWidth: 48,
  responsiveBreakpoint: 1280, // Dell 13" auto-collapse threshold

  // === TRANSITIONS ===
  transitionFast: '120ms cubic-bezier(0.4, 0, 0.2, 1)',
  transitionMedium: '200ms cubic-bezier(0.4, 0, 0.2, 1)',
  transitionSlow: '280ms cubic-bezier(0.4, 0, 0.2, 1)',

  // === SHADOWS ===
  shadowHover: '0 2px 6px rgba(0, 0, 145, 0.12)',
  shadowFocus: '0 0 0 2px rgba(10, 118, 246, 0.4)',
} as const;

/**
 * CSS global injecte une fois au mount (keyframes, focus-visible, hover-lift,
 * skeleton shimmer, checkmark stroke draw, responsive, aria-live safety).
 *
 * Adapte a l'existant : pas de CSS module, pas de build config change. Simple
 * balise <style> injectee via useLayoutEffect au mount du panel (voir
 * useEnsureAgentPanelCss dans AgentPanel.tsx).
 */
export const agentPanelCss = `
:root {
  --cerema-blue: ${T.blueMarianne};
  --cerema-blue-hover: ${T.blueMarianneHover};
  --cerema-blue-light: ${T.blueMarianneLight};
  --cerema-red: ${T.redMarianne};
  --cerema-success-fg: ${T.successFg};
  --cerema-warning-fg: ${T.warningFg};
  --cerema-error-fg: ${T.errorFg};
  --cerema-border: ${T.blueMarianneBorder};
  --cerema-panel-width: ${T.panelWidthMax}px;
}

/* Focus visible RGAA 2.4.11 — outline bleu focus */
.cerema-btn:focus-visible {
  outline: 2px solid ${T.blueFocusRing};
  outline-offset: 2px;
}

/* Hover lift — Linear/Notion pattern */
.cerema-action {
  transition: transform ${T.transitionFast}, box-shadow ${T.transitionFast},
    background ${T.transitionFast}, border-color ${T.transitionFast};
}
.cerema-action:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: ${T.shadowHover};
  border-color: ${T.blueMarianne};
}
.cerema-action:active:not(:disabled) {
  transform: translateY(0);
}

/* Skeleton shimmer — Linear/Notion loading state */
@keyframes cerema-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
.cerema-skel {
  background: linear-gradient(90deg, #eee 0%, #f7f7f7 50%, #eee 100%);
  background-size: 200% 100%;
  animation: cerema-shimmer 1.2s ease-in-out infinite;
  border-radius: ${T.radiusMd}px;
}

/* Checkmark stroke draw — action success confirmation */
@keyframes cerema-draw {
  to { stroke-dashoffset: 0; }
}
.cerema-check-svg path {
  stroke-dasharray: 30;
  stroke-dashoffset: 30;
  animation: cerema-draw 320ms ease-out forwards;
}

/* Transition collapse/expand panel */
.cerema-panel {
  transition: width ${T.transitionSlow};
  width: clamp(${T.panelWidthMin}px, 22vw, ${T.panelWidthMax}px);
}
.cerema-panel.collapsed {
  width: ${T.panelCollapsedWidth}px;
}
.cerema-panel-content {
  transition: opacity ${T.transitionMedium};
}
.cerema-panel.collapsed .cerema-panel-content {
  opacity: 0;
  pointer-events: none;
}

/* Icon buttons — history undo hover reveal */
.cerema-history-item {
  transition: background ${T.transitionFast};
}
.cerema-history-item:hover {
  background: ${T.blueMarianneLight};
}
.cerema-history-item .cerema-undo-btn {
  opacity: 0;
  transition: opacity ${T.transitionFast};
}
.cerema-history-item:hover .cerema-undo-btn,
.cerema-history-item:focus-within .cerema-undo-btn {
  opacity: 1;
}

/* Responsive Dell 13" et petits ecrans */
@media (max-width: ${T.responsiveBreakpoint}px) {
  .cerema-panel {
    width: ${T.panelWidthMin}px;
  }
}

/* Screen reader only (aria-live sans visuel) */
.cerema-sr-only {
  position: absolute;
  width: 1px; height: 1px;
  padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0, 0, 0, 0);
  white-space: nowrap; border: 0;
}
`;

/**
 * Mapper slug -> label lisible (Marie non-tech).
 * Actuellement statique iter 1 ; iter 2 lira depuis /studies/{sid}/catalog.
 */
export function friendlyBasemap(slug: string): string {
  const map: Record<string, string> = {
    'plan-ign-v2': 'Plan IGN (standard)',
    'plan-ign': 'Plan IGN (standard)',
    'ortho-hr': 'Photo aerienne',
    'ortho': 'Photo aerienne',
    'plan-scan25': 'Carte topographique IGN',
    'osm': 'OpenStreetMap',
    'osm-fr': 'OpenStreetMap France',
    'positron': 'Fond clair',
    'dark-matter': 'Fond sombre',
  };
  return map[slug] || slug;
}

export function friendlyDatasource(slug: string): string {
  const map: Record<string, string> = {
    tri_limites: 'Perimetre TRI (DGPR)',
    tri_georisques: 'Perimetre TRI (DGPR)',
    bdtopo_batiments: 'Batiments BD TOPO',
    bdtopo: 'BD TOPO IGN',
    georisques: 'Georisques',
    cadastre: 'Cadastre',
    pprn: 'PPRN',
    ppri: 'PPRi',
  };
  return map[slug] || slug;
}

export function friendlyField(slug: string): string {
  const map: Record<string, string> = {
    adresse: 'Adresse',
    numero_voie: 'Numero de voie',
    nom_voie: 'Nom de rue',
    code_postal: 'Code postal',
    commune: 'Commune',
    insee_com: 'Code INSEE commune',
    hauteur: 'Hauteur',
    nb_etages: "Nombre d'etages",
    nb_logements: 'Nombre de logements',
    usage_1: 'Usage principal',
  };
  return map[slug] || slug;
}

/**
 * Mapper block type BlockNote -> label metier Marie.
 * Deja present dans AgentPanel V1.15 (friendlyKind local) — deplace ici pour
 * usage cross-composants (BlockContextPanel futur, ZEBRA feature panel).
 */
export function friendlyKind(type: string): string {
  const map: Record<string, string> = {
    interactiveMap: 'Carte interactive',
    kpiGrid: 'Encart chiffres cles',
    kpiBadge: 'Chiffre cle',
    customHeading: 'Titre',
    customQuote: 'Citation',
    narrativeText: 'Paragraphe',
    legend: 'Legende',
    separator: 'Separateur',
    chart: 'Graphique',
    scene3d: 'Scene 3D',
    dataTable: 'Tableau',
    mediaEmbed: 'Media',
    iframeGrist: 'Widget Grist',
    heading: 'Titre',
    paragraph: 'Paragraphe',
    numberedListItem: 'Liste',
    bulletListItem: 'Liste',
  };
  return map[type] || type;
}
