/**
 * CustomHeading custom block — Vague E2 Commit F2 (D-QGIS-010).
 *
 * Mapping ComponentKind 'heading' -> BlockNote block 'customHeading'.
 * Le block 'heading' natif BlockNote utilise h1/h2/h3 mais sans
 * notre tag de level explicite. On crée un custom block dédié pour
 * coller au schema Component.params {text, level: 1-4}.
 *
 * Aligné avec helper hub _pre_render_component_html() qui rend
 * <h{level}> noir 32/26/20/16px.
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';

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
          style={{
            fontSize: SIZES[clampedLevel],
            color: '#161616',
            margin: '24px 0 12px',
            fontWeight: 700,
            lineHeight: 1.3,
          }}
        >
          {text}
        </HeadingTag>
      );
    },
  },
);
