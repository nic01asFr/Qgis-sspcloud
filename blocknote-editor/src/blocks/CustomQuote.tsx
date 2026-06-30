/**
 * CustomQuote - INLINE editable (v1.11.0 - Phase A).
 *
 * v1.10 : content: 'none' + props.text/author/source, edition via drawer.
 * v1.11 : content: 'inline' pour text + props.author/source via popup
 *         menu options (right-click ouvre le drawer pour ces 2 metas).
 *
 * Pattern UX Docs : citation = text editable inline + meta-attribution
 * configurable separement.
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
      // 'text' supprime : vient maintenant de block.content
      author: { default: '' },
      source: { default: '' },
    },
    content: 'inline' as const,
  },
  {
    render: ({ block, contentRef }) => {
      const { author, source } = block.props;
      const hasAttribution = author || source;
      const parts = [author, source].filter(Boolean).join(' · ');
      return (
        <blockquote
          onContextMenu={(e: any) => {
            e.preventDefault();
            openEditPanel(block as any);
          }}
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
          <div ref={contentRef as any} style={{ outline: 'none' }} />
          {hasAttribution && (
            <footer
              style={{
                marginTop: 8,
                fontSize: 13,
                color: '#666',
                fontStyle: 'normal',
              }}
              contentEditable={false}
            >
              — {parts}
            </footer>
          )}
        </blockquote>
      );
    },
  },
);
