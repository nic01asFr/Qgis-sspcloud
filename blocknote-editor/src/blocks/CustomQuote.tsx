/**
 * CustomQuote - INLINE editable (v1.12.2 fix).
 *
 * v1.12.2 : ref contentRef directement sur la blockquote (vs span child
 * non reconnu par BlockNote ProseMirror). Author/source en footer
 * contentEditable=false pour ne pas interferer avec l'inline editing.
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
      // v1.12.2 : wrapper avec ref direct, footer en sibling contentEditable=false
      return (
        <div
          style={{
            borderLeft: '4px solid #000091',
            padding: '12px 18px',
            margin: '18px 0',
            background: '#f4f6fa',
            color: '#1a1a1a',
            lineHeight: 1.6,
          }}
          onContextMenu={(e: any) => {
            e.preventDefault();
            openEditPanel(block as any);
          }}
        >
          {/* contentRef sur le blockquote => BlockNote pose data-node-view-content
              et injecte l'inline editable text. */}
          <blockquote
            ref={contentRef as any}
            style={{
              margin: 0,
              padding: 0,
              fontStyle: 'italic',
              fontSize: 16,
              outline: 'none',
            }}
          />
          {hasAttribution && (
            <div
              style={{
                marginTop: 8,
                fontSize: 13,
                color: '#666',
                fontStyle: 'normal',
              }}
              contentEditable={false}
              suppressContentEditableWarning
            >
              — {parts}
            </div>
          )}
        </div>
      );
    },
  },
);
