/**
 * CustomHeading - INLINE editable (v1.12.2 fix).
 *
 * v1.11/v1.12 : tentative HeadingTag dynamique + span child contentRef
 *   -> BlockNote ne pose pas data-node-view-content-react sur le span
 *   -> inline content reste vide
 *
 * v1.12.2 : switch case explicite par level + ref directement sur le node
 * (pattern BlockNote v0.22 correct = contentRef sur ELEMENT, pas span child).
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
      const style: React.CSSProperties = {
        fontSize: sizeMap[level],
        color: '#161616',
        margin: '24px 0 12px',
        fontWeight: 700,
        lineHeight: 1.3,
        outline: 'none',
      };
      const onContextMenu = (e: any) => {
        e.preventDefault();
        openEditPanel(block as any);
      };
      // v1.12.2 : switch explicite par level pour que React/JSX
      // applique correctement le ref de contentRef sur l'element HTML.
      // Le cast 'as any' contourne le typing strict mais BlockNote pose
      // bien data-node-view-content-react sur le node retourne.
      if (level === 1) {
        return <h1 ref={contentRef as any} style={style} onContextMenu={onContextMenu} />;
      }
      if (level === 2) {
        return <h2 ref={contentRef as any} style={style} onContextMenu={onContextMenu} />;
      }
      if (level === 3) {
        return <h3 ref={contentRef as any} style={style} onContextMenu={onContextMenu} />;
      }
      return <h4 ref={contentRef as any} style={style} onContextMenu={onContextMenu} />;
    },
  },
);
