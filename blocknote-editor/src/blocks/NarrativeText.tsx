/**
 * NarrativeText - rollback v1.10 pattern (v1.12.4).
 * Voir CustomHeading.tsx pour explication rollback.
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
      content: { default: '' },
    },
    content: 'none' as const,
  },
  {
    render: ({ block }) => {
      const { content } = block.props;
      return (
        <div
          onClick={(e) => { e.stopPropagation(); openEditPanel(block as any, e.nativeEvent); }}
          style={{
            fontSize: '15.5px',
            lineHeight: 1.7,
            color: '#161616',
            margin: '12px 0',
            padding: '12px 16px',
            background: '#fff',
            borderLeft: '3px solid #e5e5e5',
            borderRadius: '0 4px 4px 0',
            whiteSpace: 'pre-wrap',
            cursor: 'pointer',
          }}
        >
          {String(content || '').slice(0, 2000)}
        </div>
      );
    },
  },
);
