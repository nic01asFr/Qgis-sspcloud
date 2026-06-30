/**
 * KpiBadge custom block — Vague E2 Commit F2 (D-QGIS-010).
 *
 * Mapping ComponentKind 'kpi_badge' -> BlockNote block 'kpiBadge'.
 * Un seul KPI inline horizontal compact (vs kpi_grid pour N KPIs).
 *
 * Aligné avec helper hub _kpi_badge_partial.j2 (compact -40% hauteur
 * livré Vague E2 Commit 2 polish DSFR P5).
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { openEditPanel } from './edit-handler';

const COLOR_MAP: Record<string, string> = {
  'marianne-red': 'linear-gradient(135deg,#e1000f,#aa0000)',
  'success-green': 'linear-gradient(135deg,#1f8d4d,#0a5d2e)',
  'warning-orange': 'linear-gradient(135deg,#b34000,#cd6133)',
  'info-blue': 'linear-gradient(135deg,#000091,#0063cb)',
};

export const KpiBadgeBlock = createReactBlockSpec(
  {
    type: 'kpiBadge' as const,
    propSchema: {
      ...defaultProps,
      cid: { default: '' },
      value: { default: '' },
      label: { default: '' },
      unit: { default: '' },
      color: { default: 'info-blue' },
      source: { default: '' },
    },
    content: 'none' as const,
  },
  {
    render: ({ block }) => {
      const { value, label, unit, color, source } = block.props;
      const gradient = COLOR_MAP[String(color)] || COLOR_MAP['info-blue'];
      return (
        <div
          onClick={(e) => { e.stopPropagation(); openEditPanel(block as any, e.nativeEvent); }}
          style={{
            padding: '20px 28px',
            background: gradient,
            color: '#fff',
            borderRadius: 8,
            boxShadow: '0 2px 8px rgba(0,0,145,0.15)',
            display: 'flex',
            alignItems: 'baseline',
            gap: 16,
            flexWrap: 'wrap',
            margin: '12px 0',
          }}
        >
          <div
            style={{
              fontSize: 36,
              fontWeight: 700,
              lineHeight: 1,
              letterSpacing: '-0.5px',
            }}
          >
            {value}
            {unit && <span style={{ marginLeft: 2 }}>{unit}</span>}
          </div>
          <div
            style={{
              fontSize: 13,
              opacity: 0.95,
              textTransform: 'uppercase',
              letterSpacing: '0.8px',
              fontWeight: 600,
              flex: 1,
              minWidth: 140,
            }}
          >
            {label}
          </div>
          {source && (
            <div style={{ fontSize: 10, opacity: 0.75, fontStyle: 'italic' }}>
              {source}
            </div>
          )}
        </div>
      );
    },
  },
);
