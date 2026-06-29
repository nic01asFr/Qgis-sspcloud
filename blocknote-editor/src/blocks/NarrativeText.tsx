/**
 * NarrativeText custom block — Vague E2 Commit F3 (D-QGIS-010).
 *
 * Mapping ComponentKind 'narrative_text' -> BlockNote block 'narrativeText'.
 * Affiche le markdown content sous forme paragraph stylé (V1 simple).
 * V2 future : parser markdown -> blocks BlockNote natifs (paragraph, list, etc).
 *
 * Aligné avec helper hub _narrative_text_partial.j2 (marked.js inline).
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';

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
          }}
        >
          {String(content || '').slice(0, 2000)}
        </div>
      );
    },
  },
);
