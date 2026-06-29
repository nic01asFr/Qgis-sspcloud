/**
 * CustomQuote custom block — Vague E2 Commit F2 (D-QGIS-010).
 *
 * Mapping ComponentKind 'quote' -> BlockNote block 'customQuote'.
 * Blockquote DSFR sobre avec border-left bleu Marianne + author + source.
 *
 * Aligné avec helper hub _pre_render_component_html().
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';

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
          style={{
            borderLeft: '4px solid #000091',
            padding: '12px 18px',
            margin: '18px 0',
            background: '#f4f6fa',
            fontStyle: 'italic',
            color: '#1a1a1a',
            fontSize: 16,
            lineHeight: 1.6,
          }}
        >
          {text}
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
