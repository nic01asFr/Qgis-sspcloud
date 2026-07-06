/**
 * KpiGrid - edition inline pattern Docs (Chantier 1 V1.20.1).
 *
 * Chaque KPI value + label + unit editables inline. palette + columnsMin
 * restent dans Parametres. Ajout/suppression KPI = suggestions Assistant.
 *
 * Mapping ComponentKind 'kpi_grid' -> BlockNote block 'kpiGrid'.
 * Props kpisJson JSON serialise (BlockNote propSchema accepte pas array).
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { InlineEditable } from './InlineEditable';

const COLOR_MAP: Record<string, string> = {
  'marianne-red': 'linear-gradient(135deg,#e1000f,#aa0000)',
  'success-green': 'linear-gradient(135deg,#1f8d4d,#0a5d2e)',
  'warning-orange': 'linear-gradient(135deg,#b34000,#cd6133)',
  'info-blue': 'linear-gradient(135deg,#000091,#0063cb)',
};

const MONOCHROME_GRADIENTS = [
  'linear-gradient(135deg,#000091,#0063cb)',
  'linear-gradient(135deg,#1212a1,#1d75d0)',
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
      kpisJson: { default: '[]' },
      palette: { default: 'monochrome' },
      columnsMin: { default: 140 },
    },
    content: 'none' as const,
  },
  {
    render: ({ block, editor }) => {
      const { kpisJson, palette, columnsMin } = block.props;
      let kpis: KpiItem[] = [];
      try {
        kpis = JSON.parse(kpisJson as string);
      } catch (e) {
        return (
          <div style={{ padding: 20, color: '#888', fontStyle: 'italic' }}>
            Erreur de format des chiffres cles. Ouvrez les Parametres pour
            corriger.
          </div>
        );
      }

      const updateKpi = (idx: number, key: keyof KpiItem, next: string) => {
        const nextKpis = kpis.map((k, i) =>
          i === idx ? { ...k, [key]: next } : k,
        );
        editor.updateBlock(block, {
          props: {
            ...block.props,
            kpisJson: JSON.stringify(nextKpis),
          },
        } as any);
      };

      return (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: `repeat(auto-fit, minmax(${columnsMin}px, 1fr))`,
            gap: 12,
            margin: '12px 0',
            width: '100%',
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
                  <InlineEditable
                    as="span"
                    value={String(k.value ?? '')}
                    placeholder="0"
                    ariaLabel={`Valeur du chiffre cle ${i + 1}`}
                    onChange={(next) => updateKpi(i, 'value', next)}
                    style={{ color: '#fff' }}
                  />
                  <InlineEditable
                    as="span"
                    value={String(k.unit || '')}
                    placeholder=""
                    ariaLabel={`Unite du chiffre cle ${i + 1}`}
                    onChange={(next) => updateKpi(i, 'unit', next)}
                    style={{
                      fontSize: 14,
                      fontWeight: 500,
                      marginLeft: 4,
                      color: '#fff',
                    }}
                  />
                </div>
                <div style={{ marginTop: 6 }}>
                  <InlineEditable
                    as="div"
                    value={String(k.label || '')}
                    placeholder="Libelle"
                    ariaLabel={`Libelle du chiffre cle ${i + 1}`}
                    onChange={(next) => updateKpi(i, 'label', next)}
                    style={{
                      fontSize: 12,
                      opacity: 0.92,
                      color: '#fff',
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      );
    },
  },
);
