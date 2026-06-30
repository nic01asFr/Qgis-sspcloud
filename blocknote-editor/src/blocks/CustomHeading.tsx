/**
 * CustomHeading - INLINE editable (v1.12.3 fix definitif).
 *
 * Bug v1.12.2 : <h1 ref={contentRef as any}> ne recevait pas
 * data-node-view-content-react. Cause probable : type ref HTMLHeadingElement
 * incompatible avec le HTMLElement attendu par BlockNote, ou autre limitation
 * NodeView TipTap.
 *
 * v1.12.3 : utiliser <div> + role/aria-level pour semantique + font-size
 * stylee. Le contentRef accepte <div> sans probleme (HTMLDivElement
 * compatible avec ref BlockNote).
 *
 * Le rendu visuel est IDENTIQUE a h1/h2/h3/h4 grace au CSS font-size.
 * La semantique a11y est preservee via role="heading" aria-level.
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { openEditPanel } from './edit-handler';

export const CustomHeadingBlock = createReactBlockSpec(
  {
    type: 'customHeading' as const,
    propSchema: {
      ...defaultProps,
      cid: { default: '' },
      level: { default: 2 },
    },
    content: 'inline' as const,
  },
  {
    render: ({ block, contentRef }) => {
      const level = Math.max(1, Math.min(4, Number(block.props.level) || 2));
      const sizeMap: Record<number, string> = {
        1: '32px',
        2: '26px',
        3: '20px',
        4: '16px',
      };
      return (
        <div
          ref={contentRef}
          role="heading"
          aria-level={level}
          onContextMenu={(e: any) => {
            e.preventDefault();
            openEditPanel(block as any);
          }}
          style={{
            fontSize: sizeMap[level],
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
