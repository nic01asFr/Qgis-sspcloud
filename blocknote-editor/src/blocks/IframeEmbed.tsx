/**
 * IframeEmbed custom blocks — Vague E2 Commit F4+F5 (D-QGIS-010).
 *
 * 6 custom blocks iframe partageant le meme pattern :
 *   iframe vers /studies/{sid}/components/{cid}/render + postMessage
 *   height dynamique.
 *
 * Kinds couverts :
 * - interactive_map : carte MapLibre (Vague E2 carto enrichie symbologie/
 *                     interactions/fonds/proportional/heatmap/legende)
 * - chart           : Chart.js v4
 * - data_table      : tableau HTML
 * - scene_3d        : MapLibre fill-extrusion 3D (futur)
 * - media_embed     : video / PDF / image generique
 * - iframe_grist    : iframe Grist natif
 *
 * Reutilise le rendu Jinja2 + JS hub deja existant (D-QGIS-008 helper
 * unifie). Pas de duplication code MapLibre/ChartJS cote React.
 */
import { useEffect, useRef, useState } from 'react';
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { openEditPanel } from './edit-handler';

/**
 * Component React partagé : iframe vers /studies/{sid}/components/{cid}/render
 * + postMessage handler height dynamique.
 *
 * Communication : l'iframe partial (helper hub) envoie au mount
 *   window.parent.postMessage({type:'ready', height: <px>}, '*');
 * Le parent ajuste iframe.style.height pour eviter scroll interne.
 */
function ComponentIframe({
  sid,
  cid,
  defaultHeight = 520,
  fallbackText,
  block,
  editable = false,
}: {
  sid: string;
  cid: string;
  defaultHeight?: number;
  fallbackText?: string;
  // v1.12.1 : si block fourni + editable=true, afficher bouton flottant
  // "Modifier" au hover (l'iframe capture les clics, donc bouton overlay
  // necessaire pour acceder au drawer Edit Panel).
  block?: any;
  editable?: boolean;
}) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(defaultHeight);
  // v1.12.1 : utiliser defaultHeight comme hauteur fixe (vs postMessage
  // auto-resize qui causait scroll vertical interne quand iframe content
  // depasse). La hauteur est maintenant config par l'user via le drawer.
  useEffect(() => {
    setHeight(defaultHeight);
  }, [defaultHeight]);

  if (!sid || !cid) {
    return (
      <div
        style={{
          padding: 20,
          background: '#f9f9f9',
          color: '#888',
          fontStyle: 'italic',
          borderRadius: 6,
          margin: '12px 0',
        }}
      >
        {fallbackText || `Composant manquant (cid: ${cid || 'absent'}).`}
      </div>
    );
  }

  return (
    <div
      className="qgis-iframe-wrapper"
      style={{
        margin: '12px 0',
        border: '1px solid #e5e5e5',
        borderRadius: 6,
        overflow: 'hidden',
        width: '100%',
        position: 'relative',
      }}
    >
      <iframe
        ref={ref}
        src={`/studies/${sid}/components/${cid}/render`}
        style={{
          width: '100%',
          height,
          border: 'none',
          display: 'block',
        }}
        title={`Component ${cid}`}
      />
      {/* v1.12.1 : bouton flottant 'Modifier' visible au hover du wrapper.
          L'iframe MapLibre capture les clics pour zoom/pan, donc on ne peut
          PAS poser onClick sur le wrapper (passerait pas a l'utilisateur).
          Solution UX : bouton en overlay top-right qui ouvre le drawer. */}
      {editable && block && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            openEditPanel(block as any, e.nativeEvent);
          }}
          className="qgis-iframe-edit-btn"
          style={{
            position: 'absolute',
            top: 10,
            right: 10,
            background: '#000091',
            color: '#fff',
            border: 'none',
            borderRadius: 4,
            padding: '6px 12px',
            fontSize: 12,
            fontWeight: 600,
            cursor: 'pointer',
            boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
            zIndex: 10,
            fontFamily: 'Marianne, system-ui, sans-serif',
          }}
          title="Modifier les paramètres de la carte"
        >
          ⚙ Modifier
        </button>
      )}
    </div>
  );
}

/**
 * Lit le sid actuel depuis l'URL (parsed une fois au mount via App).
 * V1 : on stocke aussi sid dans les block.props pour simplicité.
 */
function getSidFromUrl(): string {
  const match = window.location.pathname.match(
    /^\/editor\/([0-9a-f]{12})\/assembly\/[0-9a-f]{12}/,
  );
  return match ? match[1] : '';
}

// ── interactive_map ─────────────────────────────────────────────────
// v1.12 : Composant MAP = cle metier CEREMA. PropSchema enrichi avec
// les params editables via EditPanel drawer (title/subtitle/description/
// basemap_id/source/caveat/height). Les layers/symbology/interactions
// restent dans le manifest hub (preserves au merge skip None).
export const InteractiveMapBlock = createReactBlockSpec(
  {
    type: 'interactiveMap' as const,
    propSchema: {
      ...defaultProps,
      cid: { default: '' },
      sid: { default: '' },
      // v1.12 : params editables exposes pour Edit Panel form
      title: { default: '' },
      subtitle: { default: '' },
      description: { default: '' },
      basemap_id: { default: 'osm' },
      source: { default: '' },
      caveat: { default: '' },
      height: { default: 580 },
    },
    content: 'none' as const,
  },
  {
    render: ({ block }) => {
      const height = Number(block.props.height) || 580;
      return (
        <ComponentIframe
          sid={String(block.props.sid) || getSidFromUrl()}
          cid={String(block.props.cid)}
          defaultHeight={height}
          block={block}
          editable={true}
        />
      );
    },
  },
);

// ── chart ───────────────────────────────────────────────────────────
export const ChartBlock = createReactBlockSpec(
  {
    type: 'chart' as const,
    propSchema: { ...defaultProps, cid: { default: '' }, sid: { default: '' } },
    content: 'none' as const,
  },
  {
    render: ({ block }) => (
      <ComponentIframe
        sid={String(block.props.sid) || getSidFromUrl()}
        cid={String(block.props.cid)}
        defaultHeight={400}
      />
    ),
  },
);

// ── data_table ──────────────────────────────────────────────────────
export const DataTableBlock = createReactBlockSpec(
  {
    type: 'dataTable' as const,
    propSchema: { ...defaultProps, cid: { default: '' }, sid: { default: '' } },
    content: 'none' as const,
  },
  {
    render: ({ block }) => (
      <ComponentIframe
        sid={String(block.props.sid) || getSidFromUrl()}
        cid={String(block.props.cid)}
        defaultHeight={400}
      />
    ),
  },
);

// ── scene_3d ────────────────────────────────────────────────────────
export const Scene3dBlock = createReactBlockSpec(
  {
    type: 'scene3d' as const,
    propSchema: { ...defaultProps, cid: { default: '' }, sid: { default: '' } },
    content: 'none' as const,
  },
  {
    render: ({ block }) => (
      <ComponentIframe
        sid={String(block.props.sid) || getSidFromUrl()}
        cid={String(block.props.cid)}
        defaultHeight={600}
        fallbackText="Scene 3D - helper hub _pre_render_component_html ne supporte pas encore scene_3d (Vague E3)"
      />
    ),
  },
);

// ── media_embed ─────────────────────────────────────────────────────
export const MediaEmbedBlock = createReactBlockSpec(
  {
    type: 'mediaEmbed' as const,
    propSchema: { ...defaultProps, cid: { default: '' }, sid: { default: '' } },
    content: 'none' as const,
  },
  {
    render: ({ block }) => (
      <ComponentIframe
        sid={String(block.props.sid) || getSidFromUrl()}
        cid={String(block.props.cid)}
        defaultHeight={400}
        fallbackText="Media embed (video/PDF/image) — fallback hub V0.1 (placeholder)"
      />
    ),
  },
);

// ── iframe_grist ────────────────────────────────────────────────────
export const IframeGristBlock = createReactBlockSpec(
  {
    type: 'iframeGrist' as const,
    propSchema: { ...defaultProps, cid: { default: '' }, sid: { default: '' } },
    content: 'none' as const,
  },
  {
    render: ({ block }) => (
      <ComponentIframe
        sid={String(block.props.sid) || getSidFromUrl()}
        cid={String(block.props.cid)}
        defaultHeight={500}
        fallbackText="iframe Grist — fallback hub V0.1 (placeholder)"
      />
    ),
  },
);
