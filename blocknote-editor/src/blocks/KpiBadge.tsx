/**
 * KpiBadge - edition inline pattern Docs (Chantier 1 V1.20.1).
 *
 * value + label + unit + source editables inline. color reste dans Parametres.
 *
 * Aligne avec helper hub _kpi_badge_partial.j2 (compact -40% hauteur).
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
    render: ({ block, editor }) => {
      const { value, label, unit, color, source } = block.props;
      const gradient = COLOR_MAP[String(color)] || COLOR_MAP['info-blue'];
      const updateProp = (key: string, next: string) => {
        editor.updateBlock(block, {
          props: { ...block.props, [key]: next },
        } as any);
      };
      return (
        <div
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
              display: 'flex',
              alignItems: 'baseline',
              gap: 2,
            }}
          >
            <InlineEditable
              as="span"
              value={String(value || '')}
              placeholder="0"
              ariaLabel="Modifier la valeur"
              onChange={(next) => updateProp('value', next)}
              style={{ color: '#fff' }}
            />
            <InlineEditable
              as="span"
              value={String(unit || '')}
              placeholder="unite"
              ariaLabel="Modifier l'unite"
              onChange={(next) => updateProp('unit', next)}
              style={{ fontSize: 24, color: '#fff', opacity: 0.9 }}
            />
          </div>
          <div style={{ flex: 1, minWidth: 140 }}>
            <InlineEditable
              as="div"
              value={String(label || '')}
              placeholder="Libelle de l'indicateur"
              ariaLabel="Modifier le libelle"
              onChange={(next) => updateProp('label', next)}
              style={{
                fontSize: 13,
                opacity: 0.95,
                textTransform: 'uppercase',
                letterSpacing: '0.8px',
                fontWeight: 600,
                color: '#fff',
              }}
            />
          </div>
          <InlineEditable
            as="div"
            value={String(source || '')}
            placeholder="Source (optionnel)…"
            ariaLabel="Modifier la source"
            onChange={(next) => updateProp('source', next)}
            style={{
              fontSize: 10,
              opacity: source ? 0.75 : 0.5,
              fontStyle: 'italic',
              color: '#fff',
            }}
          />
        </div>
      );
    },
  },
);
