/**
 * CustomHeading - rollback v1.10 pattern (v1.12.4).
 *
 * v1.11/v1.12.x ont tente d'utiliser content:'inline' editable mais
 * BlockNote v0.22 NodeView React ne semble pas attacher correctement
 * data-node-view-content-react sur les custom blocks malgre le pattern
 * 'contentRef'. 3 tentatives ont echoue (h1/h2/h3/h4 root, span child,
 * div+ARIA).
 *
 * v1.12.4 : rollback pragmatique vers content:'none' + props.text.
 * Marie utilise le drawer Edit Panel pour modifier (click sur le block).
 * Au moins le texte s'AFFICHE correctement (le bug critique 'textes vides').
 *
 * Inline editing reporte V1.13 / V2 (besoin investigation BlockNote v0.22
 * API plus profonde ou upgrade vers BlockNote v0.23+).
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
      text: { default: '' },
    },
    content: 'none' as const,
  },
  {
    render: ({ block }) => {
      const { level, text } = block.props;
      const clampedLevel = Math.max(1, Math.min(4, Number(level)));
      const HeadingTag = (`h${clampedLevel}` as unknown) as keyof JSX.IntrinsicElements;
      return (
        <HeadingTag
          onClick={(e: any) => { e.stopPropagation(); openEditPanel(block as any, e.nativeEvent); }}
          style={{
            fontSize: SIZES[clampedLevel],
            color: '#161616',
            margin: '24px 0 12px',
            fontWeight: 700,
            lineHeight: 1.3,
            cursor: 'pointer',
          }}
        >
          {String(text || '')}
        </HeadingTag>
      );
    },
  },
);
