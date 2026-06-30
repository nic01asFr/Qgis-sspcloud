/**
 * NarrativeText - INLINE editable (v1.12.2 fix).
 *
 * v1.12.2 : ref contentRef directement sur le div interieur (vs nested
 * span/div qui causait data-node-view-content-react manquant).
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
    },
    content: 'inline' as const,
  },
  {
    render: ({ block, contentRef }) => (
      <div
        ref={contentRef as any}
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
          outline: 'none',
        }}
      />
    ),
  },
);
