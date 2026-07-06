/**
 * NarrativeText — edition inline multiline pattern Docs (Chantier 1 V1.20.1).
 *
 * Le contenu markdown est edite directement dans le doc. Bold/italic/link via
 * toolbar flottante BubbleMenu native BlockNote (a activer separement).
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { InlineEditable } from './InlineEditable';

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
    render: ({ block, editor }) => {
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
          }}
        >
          <InlineEditable
            as="div"
            value={String(content || '')}
            placeholder="Décrivez votre analyse ou votre observation…"
            multiline
            ariaLabel="Modifier le paragraphe narratif"
            onChange={(next) => {
              editor.updateBlock(block, {
                props: { ...block.props, content: next },
              } as any);
            }}
            style={{ whiteSpace: 'pre-wrap', display: 'block' }}
          />
        </div>
      );
    },
  },
);
