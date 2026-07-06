/**
 * CustomHeading — edition inline directe pattern Docs (Chantier 1 V1.20.1).
 *
 * Historique :
 * - v1.10 : content: 'none' + drawer overlay au clic
 * - v1.11/v1.12.x : 3 tentatives content: 'inline' echouees BlockNote v0.22
 * - v1.12.4 rollback vers content: 'none' + props.text + panel Parametres
 * - **V1.20.1 Chantier 1 (2026-07-06)** : pattern InlineEditable
 *   contentEditable + editor.updateBlock. Marie edite directement dans le doc.
 *
 * Le niveau (H1-H4) reste modifiable via onglet Parametres du panel unifie
 * (contextuel a la selection). Le texte est lui edite inline (comme Docs).
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { InlineEditable } from './InlineEditable';

const SIZES: Record<number, string> = {
  1: '32px',
  2: '26px',
  3: '20px',
  4: '16px',
};

export const CustomHeadingBlock = createReactBlockSpec(
  {
    type: 'customHeading' as const,
    propSchema: {
      ...defaultProps,
      cid: { default: '' },
      level: { default: 2 },
      text: { default: '' },
    },
    content: 'none' as const,
  },
  {
    render: ({ block, editor }) => {
      const { level, text } = block.props;
      const clampedLevel = Math.max(1, Math.min(4, Number(level)));
      const tag = `h${clampedLevel}` as 'h1' | 'h2' | 'h3' | 'h4';
      return (
        <InlineEditable
          as={tag}
          value={String(text || '')}
          placeholder={`Titre niveau ${clampedLevel}…`}
          multiline={false}
          ariaLabel={`Modifier le titre H${clampedLevel}`}
          onChange={(next) => {
            editor.updateBlock(block, {
              props: { ...block.props, text: next },
            } as any);
          }}
          style={{
            fontSize: SIZES[clampedLevel],
            color: '#161616',
            margin: '24px 0 12px',
            fontWeight: 700,
            lineHeight: 1.3,
            display: 'block',
          }}
        />
      );
    },
  },
);
