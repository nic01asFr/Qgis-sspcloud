/**
 * CustomQuote - INLINE editable (v1.12.3 fix definitif).
 *
 * Pattern : <div ref={contentRef}> root direct + footer attribution sibling
 * contentEditable=false (pas de blockquote pour eviter incompatibilite ref).
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
        <div
          ref={contentRef}
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
            outline: 'none',
            position: 'relative',
          }}
        >
          {/* Footer attribution rendu via CSS pseudo-element ou positionne en sticky bottom
              pour ne pas interferer avec l'inline editing du contenu principal */}
          {hasAttribution && (
            <span
              contentEditable={false}
              suppressContentEditableWarning
              style={{
                display: 'block',
                marginTop: 8,
                fontSize: 13,
                color: '#666',
                fontStyle: 'normal',
                userSelect: 'none',
              }}
            >
              — {parts}
            </span>
          )}
        </div>
      );
    },
  },
);
