/**
 * Legend - edition inline pattern Docs (Chantier 1 V1.20.1).
 *
 * title + source + chaque item.label editables inline. color + count restent
 * dans Parametres (structure JSON complexe).
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { InlineEditable } from './InlineEditable';

interface LegendItem {
  label: string;
  color: string;
  count?: number;
}

export const LegendBlock = createReactBlockSpec(
  {
    type: 'legend' as const,
    propSchema: {
      ...defaultProps,
      cid: { default: '' },
      itemsJson: { default: '[]' },
      title: { default: '' },
      source: { default: '' },
    },
    content: 'none' as const,
  },
  {
    render: ({ block, editor }) => {
      const { itemsJson, title, source } = block.props;
      let items: LegendItem[] = [];
      try {
        items = JSON.parse(itemsJson as string);
      } catch (e) {
        items = [];
      }
      const updateProp = (key: string, next: string) => {
        editor.updateBlock(block, {
          props: { ...block.props, [key]: next },
        } as any);
      };
      const updateItemLabel = (idx: number, next: string) => {
        const nextItems = items.map((it, i) =>
          i === idx ? { ...it, label: next } : it,
        );
        editor.updateBlock(block, {
          props: { ...block.props, itemsJson: JSON.stringify(nextItems) },
        } as any);
      };
      return (
        <div
          style={{
            padding: '12px 16px',
            background: '#fafafa',
            borderRadius: 6,
            border: '1px solid #e5e5e5',
            fontSize: 12,
            color: '#444',
            margin: '12px 0',
          }}
        >
          <InlineEditable
            as="div"
            value={String(title || '')}
            placeholder="Titre de la legende…"
            ariaLabel="Modifier le titre de la legende"
            onChange={(next) => updateProp('title', next)}
            style={{
              fontWeight: 600,
              color: '#000091',
              marginBottom: 8,
              fontSize: 13,
            }}
          />
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 14,
              alignItems: 'center',
            }}
          >
            {items.map((item, i) => (
              <span
                key={i}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
              >
                <span
                  style={{
                    display: 'inline-block',
                    width: 14,
                    height: 14,
                    background: item.color,
                    borderRadius: 3,
                    border: '1px solid rgba(0,0,0,.1)',
                  }}
                />
                <InlineEditable
                  as="span"
                  value={String(item.label || '')}
                  placeholder="Etiquette…"
                  ariaLabel={`Modifier l'etiquette ${i + 1}`}
                  onChange={(next) => updateItemLabel(i, next)}
                />
                {item.count !== undefined && (
                  <em style={{ color: '#888', marginLeft: 4 }}>
                    ({item.count})
                  </em>
                )}
              </span>
            ))}
          </div>
          <div
            style={{
              marginTop: 8,
              fontSize: 11,
              color: '#666',
              fontStyle: 'italic',
              display: 'flex',
              gap: 4,
            }}
          >
            <span aria-hidden="true">Source :</span>
            <InlineEditable
              as="span"
              value={String(source || '')}
              placeholder="Ajouter une source…"
              ariaLabel="Modifier la source"
              onChange={(next) => updateProp('source', next)}
              style={{ fontStyle: 'italic' }}
            />
          </div>
        </div>
      );
    },
  },
);
