/**
 * CustomHeading - INLINE editable (v1.11.0 - Phase A).
 *
 * Avant v1.11 : content: 'none' + props.text, edition via drawer.
 * Apres v1.11 : content: 'inline' (BlockNote gere text edition native),
 *               props.level + cid restent. Edition INLINE comme Docs.
 *
 * Marie clique le titre, caret apparait, tape directement.
 * Niveau ajustable via menu options du bloc (popup ou shortcut).
 *
 * Pattern UX aligne sur Docs LaSuite : titre = text editable inline,
 * niveau = meta-prop ajustable separement.
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { openEditPanel } from './edit-handler';

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
      // 'text' supprime du propSchema : le contenu vient maintenant de
      // block.content (BlockNote inline editing native).
    },
    // v1.11 Phase A : content: 'inline' permet edition native BlockNote.
    content: 'inline' as const,
  },
  {
    render: ({ block, contentRef }) => {
      const { level } = block.props;
      const clampedLevel = Math.max(1, Math.min(4, Number(level)));
      const HeadingTag = (`h${clampedLevel}` as unknown) as keyof JSX.IntrinsicElements;
      return (
        <HeadingTag
          ref={contentRef as any}
          // Right-click ouvre le menu d'options BlockNote natif (Supprimer,
          // Couleurs, Niveau >). Click simple = caret pour edition inline.
          onContextMenu={(e: any) => {
            e.preventDefault();
            openEditPanel(block as any);
          }}
          style={{
            fontSize: SIZES[clampedLevel],
            color: '#161616',
            margin: '24px 0 12px',
            fontWeight: 700,
            lineHeight: 1.3,
            outline: 'none',
          }}
        />
      );
    },
  },
);
