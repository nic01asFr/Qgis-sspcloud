/**
 * NarrativeText - INLINE editable (v1.11.0 - Phase A).
 *
 * v1.10 : content: 'none' + props.content markdown, edition via drawer.
 * v1.11 : content: 'inline' pour edition native BlockNote.
 *
 * Pattern UX Docs : texte narratif = paragraph editable inline avec
 * markdown shortcuts (**gras**, *italique*, [lien]) supportes nativement
 * par BlockNote.
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { openEditPanel } from './edit-handler';

export const NarrativeTextBlock = createReactBlockSpec(
  {
    type: 'narrativeText' as const,
    propSchema: {
      ...defaultProps,
      cid: { default: '' },
      // 'content' supprime du propSchema : remplace par block.content inline
    },
    content: 'inline' as const,
  },
  {
    render: ({ block, contentRef }) => {
      return (
        <div
          onContextMenu={(e: any) => {
            e.preventDefault();
            openEditPanel(block as any);
          }}
          style={{
            fontSize: '15.5px',
            lineHeight: 1.7,
            color: '#161616',
            margin: '12px 0',
            padding: '12px 16px',
            background: '#fff',
            borderLeft: '3px solid #e5e5e5',
            borderRadius: '0 4px 4px 0',
          }}
        >
          <div ref={contentRef as any} style={{ outline: 'none' }} />
        </div>
      );
    },
  },
);
