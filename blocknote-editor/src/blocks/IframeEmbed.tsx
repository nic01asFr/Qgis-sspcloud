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
}: {
  sid: string;
  cid: string;
  defaultHeight?: number;
  fallbackText?: string;
}) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(defaultHeight);

  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (
        e.data &&
        typeof e.data === 'object' &&
        e.data.type === 'ready' &&
        typeof e.data.height === 'number' &&
        ref.current &&
        e.source === ref.current.contentWindow
      ) {
        setHeight(Math.max(200, Math.min(1200, e.data.height)));
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

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
      style={{
        margin: '12px 0',
        border: '1px solid #e5e5e5',
        borderRadius: 6,
        overflow: 'hidden',
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
export const InteractiveMapBlock = createReactBlockSpec(
  {
    type: 'interactiveMap' as const,
    propSchema: { ...defaultProps, cid: { default: '' }, sid: { default: '' } },
    content: 'none' as const,
  },
  {
    render: ({ block }) => (
      <ComponentIframe
        sid={String(block.props.sid) || getSidFromUrl()}
        cid={String(block.props.cid)}
        defaultHeight={580}
      />
    ),
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
