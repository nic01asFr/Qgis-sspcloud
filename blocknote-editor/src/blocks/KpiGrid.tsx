/**
 * KpiGrid custom block — Vague E2 Commit F1 (D-QGIS-010).
 *
 * Mapping ComponentKind 'kpi_grid' -> BlockNote block 'kpiGrid'.
 * Pattern de référence pour les autres custom blocks DOM (F2-F3).
 *
 * Props mapping `block.props` ↔ `Component.params` :
 *   cid           : référence vers Component existant (12 hex)
 *   kpisJson      : JSON string [{value, label, unit?, color?}]
 *                   (BlockNote propSchema accepte string/number/boolean
 *                    pas dict/array, on stocke JSON serialisé)
 *   palette       : 'monochrome' | 'rainbow' (default monochrome)
 *   columnsMin    : px largeur min des chips (default 140)
 *
 * Rendu : grid CSS auto-fit + chips colorés gradient bleu marianne monochrome.
 * Aligné avec helper hub _pre_render_component_html() côté Jinja2.
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';

const COLOR_MAP: Record<string, string> = {
  'marianne-red': 'linear-gradient(135deg,#e1000f,#aa0000)',
  'success-green': 'linear-gradient(135deg,#1f8d4d,#0a5d2e)',
  'warning-orange': 'linear-gradient(135deg,#b34000,#cd6133)',
  'info-blue': 'linear-gradient(135deg,#000091,#0063cb)',
};

const MONOCHROME_GRADIENTS = [
  'linear-gradient(135deg,#000091,#0063cb)',  // bleu foncé
  'linear-gradient(135deg,#1212a1,#1d75d0)',  // légèrement plus clair
  'linear-gradient(135deg,#2424b0,#3d87d4)',
  'linear-gradient(135deg,#3636bf,#5099d7)',
];

interface KpiItem {
  value: string | number;
  label: string;
  unit?: string;
  color?: string;
}

export const KpiGridBlock = createReactBlockSpec(
  {
    type: 'kpiGrid' as const,
    propSchema: {
      ...defaultProps,
      cid: { default: '' },
      kpisJson: { default: '[]' }, // JSON serialisé (BlockNote propSchema limit)
      palette: { default: 'monochrome' },
      columnsMin: { default: 140 },
    },
    content: 'none' as const,
  },
  {
    render: ({ block }) => {
      const { kpisJson, palette, columnsMin } = block.props;
      let kpis: KpiItem[] = [];
      try {
        kpis = JSON.parse(kpisJson as string);
      } catch (e) {
        // KPIs JSON malformés, afficher fallback
        return (
          <div style={{ padding: 20, color: '#888', fontStyle: 'italic' }}>
            kpi_grid : JSON invalide (cf. props.kpisJson)
          </div>
        );
      }

      return (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: `repeat(auto-fit, minmax(${columnsMin}px, 1fr))`,
            gap: 12,
            margin: '12px 0',
          }}
        >
          {kpis.slice(0, 24).map((k, i) => {
            const userColor = k.color;
            let gradient: string;
            if (userColor && COLOR_MAP[userColor]) {
              gradient = COLOR_MAP[userColor];
            } else if (palette === 'monochrome') {
              gradient = MONOCHROME_GRADIENTS[i % MONOCHROME_GRADIENTS.length];
            } else {
              gradient = COLOR_MAP['info-blue'];
            }
            return (
              <div
                key={i}
                style={{
                  background: gradient,
                  color: '#fff',
                  padding: '18px 14px',
                  borderRadius: 6,
                  textAlign: 'center',
                  boxShadow: '0 1px 3px rgba(0,0,0,.08)',
                }}
              >
                <div style={{ fontSize: 28, fontWeight: 700, lineHeight: 1.1 }}>
                  {k.value}
                  {k.unit && (
                    <span
                      style={{
                        fontSize: 14,
                        fontWeight: 500,
                        marginLeft: 4,
                      }}
                    >
                      {k.unit}
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 12, marginTop: 6, opacity: 0.92 }}>
                  {k.label}
                </div>
              </div>
            );
          })}
        </div>
      );
    },
  },
);
