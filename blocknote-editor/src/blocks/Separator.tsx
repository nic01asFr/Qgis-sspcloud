/**
 * Separator custom block — Vague E2 Commit F2 (D-QGIS-010).
 *
 * Mapping ComponentKind 'separator' -> BlockNote block 'separator'.
 * HR stylisé avec variants rule/ornament/break.
 *
 * Aligné avec helper hub _pre_render_component_html() Vague E2 Commit 2.
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';

export const SeparatorBlock = createReactBlockSpec(
  {
    type: 'separator' as const,
    propSchema: {
      ...defaultProps,
      cid: { default: '' },
      style: { default: 'solid' },
      color: { default: '#000091' },
      variant: { default: 'rule' },
    },
    content: 'none' as const,
  },
  {
    render: ({ block }) => {
      let { style, color, variant } = block.props;
      const styleStr = String(style);
      const colorStr = String(color);
      const variantStr = String(variant);
      const validStyle = ['solid', 'dashed', 'dotted'].includes(styleStr)
        ? styleStr
        : 'solid';
      const validColor =
        colorStr.startsWith('#') && colorStr.length <= 7 ? colorStr : '#000091';

      if (variantStr === 'ornament') {
        return (
          <hr
            style={{
              border: 'none',
              borderTop: `3px ${validStyle} ${validColor}`,
              margin: '48px auto',
              width: 80,
            }}
          />
        );
      }
      if (variantStr === 'break') {
        return <div style={{ height: 60 }} />;
      }
      return (
        <hr
          style={{
            border: 'none',
            borderTop: `2px ${validStyle} ${validColor}`,
            margin: '40px 0',
            width: '100%',
            opacity: 0.6,
          }}
        />
      );
    },
  },
);
