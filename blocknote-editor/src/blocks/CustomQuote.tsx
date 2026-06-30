/**
 * CustomQuote - rollback v1.10 pattern (v1.12.4).
 * Voir CustomHeading.tsx pour explication rollback.
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { openEditPanel } from './edit-handler';

export const CustomQuoteBlock = createReactBlockSpec(
  {
    type: 'customQuote' as const,
    propSchema: {
      ...defaultProps,
      cid: { default: '' },
      text: { default: '' },
      author: { default: '' },
      source: { default: '' },
    },
    content: 'none' as const,
  },
  {
    render: ({ block }) => {
      const { text, author, source } = block.props;
      const hasAttribution = author || source;
      const parts = [author, source].filter(Boolean).join(' · ');
      return (
        <blockquote
          onClick={(e) => { e.stopPropagation(); openEditPanel(block as any, e.nativeEvent); }}
          style={{
            borderLeft: '4px solid #000091',
            padding: '12px 18px',
            margin: '18px 0',
            background: '#f4f6fa',
            fontStyle: 'italic',
            color: '#1a1a1a',
            fontSize: 16,
            lineHeight: 1.6,
            cursor: 'pointer',
          }}
        >
          {String(text || '')}
          {hasAttribution && (
            <footer
              style={{
                marginTop: 8,
                fontSize: 13,
                color: '#666',
                fontStyle: 'normal',
              }}
            >
              — {parts}
            </footer>
          )}
        </blockquote>
      );
    },
  },
);
