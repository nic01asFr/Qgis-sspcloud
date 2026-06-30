/**
 * EditPanel — drawer latéral droit pour éditer les params d'un custom block.
 *
 * Sprint 4 Vague E3 (option A, item 8.18) - v1.10.0.
 *
 * Résout le gap UX P0 : les 7 DOM custom blocks (kpi_grid, kpi_badge,
 * heading, quote, legend, narrative_text, separator) ont `content: 'none'`
 * → non éditables inline dans BlockNote. Le drawer expose un formulaire
 * HTML par kind pour modifier les params + déclenche updateComponent au save.
 *
 * Pour les 6 iframe kinds (interactive_map/chart/data_table/scene_3d/
 * media_embed/iframe_grist) : drawer en mode lecture seule + lien vers
 * desk pour modifier scene_manifest (V2 8.18b couvrira l'édition complète).
 *
 * Communication block ↔ panel via global `window.__openEditPanel(block)` :
 * - Évite le prop drilling à travers BlockNote createReactBlockSpec API
 * - Le bloc clic → invoke global → App state setter → drawer affiche
 */
import { useState, useEffect } from 'react';
import { updateComponent } from './api';
import { KpiGridForm } from './forms/KpiGridForm';
import { KpiBadgeForm } from './forms/KpiBadgeForm';
import { HeadingForm } from './forms/HeadingForm';
import { QuoteForm } from './forms/QuoteForm';
import { NarrativeTextForm } from './forms/NarrativeTextForm';
import { LegendForm } from './forms/LegendForm';
import { SeparatorForm } from './forms/SeparatorForm';

export interface EditableBlock {
  id: string;        // ID interne BlockNote
  type: string;      // ex 'kpiGrid', 'customHeading'...
  props: Record<string, any>;
}

interface EditPanelProps {
  block: EditableBlock | null;
  sid: string;
  versionNumSource: number | null;
  onClose: () => void;
  onSaved: (newProps: Record<string, any>) => void;
}

type SaveStatus =
  | { type: 'idle' }
  | { type: 'saving' }
  | { type: 'saved'; cid: string; versionNum: number }
  | { type: 'error'; message: string }
  | { type: 'conflict'; current: number; source: number };

const DOM_KIND_LABELS: Record<string, string> = {
  kpiGrid: 'Bandeau KPIs (kpi_grid)',
  kpiBadge: 'KPI isolé (kpi_badge)',
  customHeading: 'Titre (heading)',
  customQuote: 'Citation (quote)',
  legend: 'Légende',
  narrativeText: 'Texte narratif (markdown)',
  separator: 'Séparateur',
};

const IFRAME_KIND_LABELS: Record<string, string> = {
  interactiveMap: 'Carte interactive',
  chart: 'Graphique',
  dataTable: 'Tableau de données',
  scene3d: 'Scène 3D',
  mediaEmbed: 'Média intégré',
  iframeGrist: 'Widget Grist',
};

export function EditPanel({
  block,
  sid,
  versionNumSource,
  onClose,
  onSaved,
}: EditPanelProps) {
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [status, setStatus] = useState<SaveStatus>({ type: 'idle' });

  useEffect(() => {
    if (block) {
      setFormData({ ...block.props });
      setStatus({ type: 'idle' });
    }
  }, [block]);

  if (!block) return null;

  const isDom = block.type in DOM_KIND_LABELS;
  const isIframe = block.type in IFRAME_KIND_LABELS;
  const label = DOM_KIND_LABELS[block.type] || IFRAME_KIND_LABELS[block.type] || block.type;
  const cid = String(block.props.cid || '');

  // Mapping kind → ComponentKind snake_case pour le manifest hub
  const BLOCK_TO_KIND: Record<string, string> = {
    kpiGrid: 'kpi_grid',
    kpiBadge: 'kpi_badge',
    customHeading: 'heading',
    customQuote: 'quote',
    legend: 'legend',
    narrativeText: 'narrative_text',
    separator: 'separator',
  };

  const handleSave = async () => {
    if (!cid) {
      setStatus({ type: 'error', message: 'cid manquant — composant non sauvegardable' });
      return;
    }
    if (!isDom) {
      setStatus({ type: 'error', message: 'Édition iframe kinds : V2 8.18b à venir' });
      return;
    }
    setStatus({ type: 'saving' });

    // Reconstruire le manifest Component depuis formData (props -> params)
    const componentKind = BLOCK_TO_KIND[block.type];
    const params = buildParamsFromFormData(componentKind, formData);
    const manifest = {
      kind: componentKind,
      title: deriveTitle(componentKind, params),
      classification: 'cerema_internal',
      params,
      rendering: { runtime: 'html', container_size: 'full' },
    };

    const result = await updateComponent(sid, cid, manifest, versionNumSource);
    if (result.ok && result.newVersionNum) {
      setStatus({ type: 'saved', cid, versionNum: result.newVersionNum });
      // Notifier le parent : convertir params back -> block.props pour MAJ BlockNote
      const newBlockProps = paramsToBlockProps(block.type, params);
      onSaved({ ...newBlockProps, cid });
      // Auto-close après 1.5s
      setTimeout(() => onClose(), 1500);
    } else if (result.conflict) {
      setStatus({
        type: 'conflict',
        current: result.conflict.currentVersionNum,
        source: result.conflict.sourceVersionNum,
      });
    } else {
      setStatus({ type: 'error', message: result.error || 'Erreur inconnue' });
    }
  };

  return (
    <>
      {/* Overlay backdrop semi-transparent */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.3)',
          zIndex: 998,
        }}
      />
      {/* Drawer droit fixed */}
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-panel-title"
        style={{
          position: 'fixed',
          right: 0,
          top: 0,
          bottom: 0,
          width: 420,
          background: '#fff',
          boxShadow: '-8px 0 24px rgba(0,0,0,0.15)',
          zIndex: 999,
          display: 'flex',
          flexDirection: 'column',
          fontFamily: 'Marianne, system-ui, sans-serif',
        }}
      >
        <header
          style={{
            padding: '14px 20px',
            background: '#000091',
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
          }}
        >
          <div>
            <div id="edit-panel-title" style={{ fontWeight: 600, fontSize: 14 }}>
              Modifier — {label}
            </div>
            {cid && (
              <div style={{ fontSize: 10, opacity: 0.8, marginTop: 2 }}>
                cid : {cid.slice(0, 12)}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Fermer"
            style={{
              background: 'none',
              border: 'none',
              color: '#fff',
              fontSize: 24,
              cursor: 'pointer',
              padding: '0 4px',
              lineHeight: 1,
            }}
          >
            ×
          </button>
        </header>

        <div style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
          {isIframe && (
            <div
              style={{
                padding: 16,
                background: '#fff5e6',
                border: '1px solid #ffcb8c',
                borderRadius: 6,
                fontSize: 13,
                color: '#7a4400',
              }}
            >
              <strong>Lecture seule en V1.10</strong>
              <p style={{ margin: '8px 0 0', lineHeight: 1.5 }}>
                Les composants {label.toLowerCase()} dépendent d'un{' '}
                <code>scene_manifest</code> QGIS. Pour les modifier, retourne
                sur le desk → recipe ou demande à l'assistant IA. Édition
                directe via cette interface : Vague E3 V2 (item 8.18b).
              </p>
            </div>
          )}

          {!isIframe && block.type === 'kpiGrid' && (
            <KpiGridForm data={formData} onChange={setFormData} />
          )}
          {!isIframe && block.type === 'kpiBadge' && (
            <KpiBadgeForm data={formData} onChange={setFormData} />
          )}
          {!isIframe && block.type === 'customHeading' && (
            <HeadingForm data={formData} onChange={setFormData} />
          )}
          {!isIframe && block.type === 'customQuote' && (
            <QuoteForm data={formData} onChange={setFormData} />
          )}
          {!isIframe && block.type === 'narrativeText' && (
            <NarrativeTextForm data={formData} onChange={setFormData} />
          )}
          {!isIframe && block.type === 'legend' && (
            <LegendForm data={formData} onChange={setFormData} />
          )}
          {!isIframe && block.type === 'separator' && (
            <SeparatorForm data={formData} onChange={setFormData} />
          )}
        </div>

        {/* Footer status + actions */}
        <footer
          style={{
            padding: 16,
            borderTop: '1px solid #ddd',
            background: '#f6f6f6',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          {status.type === 'saving' && (
            <div style={{ fontSize: 12, color: '#0063cb' }}>
              Sauvegarde en cours…
            </div>
          )}
          {status.type === 'saved' && (
            <div style={{ fontSize: 12, color: '#1f8d4d' }}>
              ✓ Sauvegardé — version {status.versionNum}
            </div>
          )}
          {status.type === 'error' && (
            <div style={{ fontSize: 12, color: '#a50f15' }}>
              ⚠ {status.message}
            </div>
          )}
          {status.type === 'conflict' && (
            <div style={{ fontSize: 12, color: '#a50f15' }}>
              ⚠ Conflit (serveur v{status.current}, vous v{status.source}) —
              fermez et rechargez la page
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: '8px 16px',
                fontSize: 13,
                border: '1px solid #ccc',
                background: '#fff',
                color: '#333',
                borderRadius: 4,
                cursor: 'pointer',
              }}
            >
              Annuler
            </button>
            {isDom && (
              <button
                type="button"
                onClick={handleSave}
                disabled={status.type === 'saving' || !cid}
                style={{
                  padding: '8px 16px',
                  fontSize: 13,
                  border: 'none',
                  background: '#000091',
                  color: '#fff',
                  borderRadius: 4,
                  cursor: status.type === 'saving' ? 'wait' : 'pointer',
                  fontWeight: 600,
                  opacity: !cid ? 0.5 : 1,
                }}
              >
                💾 Enregistrer
              </button>
            )}
          </div>
        </footer>
      </aside>
    </>
  );
}

// ── Helpers de conversion formData ↔ component manifest ──

function buildParamsFromFormData(
  kind: string,
  data: Record<string, any>,
): Record<string, any> {
  switch (kind) {
    case 'kpi_grid':
      return {
        kpis: (() => {
          try {
            return typeof data.kpisJson === 'string'
              ? JSON.parse(data.kpisJson)
              : data.kpisJson || [];
          } catch {
            return [];
          }
        })(),
        palette: data.palette || 'monochrome',
        columns_min: Number(data.columnsMin || 140),
      };
    case 'kpi_badge':
      return {
        value: String(data.value || ''),
        label: String(data.label || ''),
        unit: String(data.unit || ''),
        color: String(data.color || 'info-blue'),
        source: String(data.source || ''),
      };
    case 'heading':
      return {
        text: String(data.text || ''),
        level: Number(data.level || 2),
      };
    case 'quote':
      return {
        text: String(data.text || ''),
        author: String(data.author || ''),
        source: String(data.source || ''),
      };
    case 'narrative_text':
      return { content: String(data.content || '') };
    case 'legend':
      return {
        items: (() => {
          try {
            return typeof data.itemsJson === 'string'
              ? JSON.parse(data.itemsJson)
              : data.itemsJson || [];
          } catch {
            return [];
          }
        })(),
        title: String(data.title || ''),
        source: String(data.source || ''),
      };
    case 'separator':
      return {
        style: String(data.style || 'solid'),
        color: String(data.color || '#000091'),
        variant: String(data.variant || 'rule'),
      };
    default:
      return data;
  }
}

function paramsToBlockProps(
  blockType: string,
  params: Record<string, any>,
): Record<string, any> {
  switch (blockType) {
    case 'kpiGrid':
      return {
        kpisJson: JSON.stringify(params.kpis || []),
        palette: params.palette || 'monochrome',
        columnsMin: Number(params.columns_min || 140),
      };
    case 'legend':
      return {
        itemsJson: JSON.stringify(params.items || []),
        title: params.title || '',
        source: params.source || '',
      };
    case 'kpiBadge':
    case 'customHeading':
    case 'customQuote':
    case 'narrativeText':
    case 'separator':
      return { ...params };
    default:
      return { ...params };
  }
}

function deriveTitle(kind: string, params: Record<string, any>): string {
  const candidate = params.text || params.label || params.title || params.content;
  if (candidate && typeof candidate === 'string') {
    return candidate.slice(0, 80);
  }
  return kind;
}
