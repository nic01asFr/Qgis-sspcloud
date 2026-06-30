/**
 * Legend custom block — Vague E2 Commit F3 (D-QGIS-010).
 *
 * Mapping ComponentKind 'legend' -> BlockNote block 'legend'.
 * Chips couleur + items + source datée (DSFR sobre).
 *
 * V1 : format 'chips' uniquement (formats gradient_bar / proportional
 * réservés aux légendes auto-dérivées des cartes Vague E2 Commit 10).
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { openEditPanel } from './edit-handler';

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
    render: ({ block }) => {
      const { itemsJson, title, source } = block.props;
      let items: LegendItem[] = [];
      try {
        items = JSON.parse(itemsJson as string);
      } catch (e) {
        items = [];
      }
      return (
        <div
          onClick={(e) => { e.stopPropagation(); openEditPanel(block as any, e.nativeEvent); }}
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
          {title && (
            <div
              style={{
                fontWeight: 600,
                color: '#000091',
                marginBottom: 8,
                fontSize: 13,
              }}
            >
              {title}
            </div>
          )}
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
                <span>
                  {item.label}
                  {item.count !== undefined && (
                    <em style={{ color: '#888', marginLeft: 4 }}>
                      ({item.count})
                    </em>
                  )}
                </span>
              </span>
            ))}
          </div>
          {source && (
            <div
              style={{
                marginTop: 8,
                fontSize: 11,
                color: '#666',
                fontStyle: 'italic',
              }}
            >
              Source : {source}
            </div>
          )}
        </div>
      );
    },
  },
);
