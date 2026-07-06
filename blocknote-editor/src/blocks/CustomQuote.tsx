/**
 * CustomQuote — edition inline pattern Docs (Chantier 1 V1.20.1).
 *
 * 3 champs editables inline : text (multiline) + author + source.
 * Voir CustomHeading.tsx pour explication migration.
 */
import { createReactBlockSpec } from '@blocknote/react';
import { defaultProps } from '@blocknote/core';
import { InlineEditable } from './InlineEditable';

export const CustomQuoteBlock = createReactBlockSpec(
  {
    type: 'customQuote' as const,
    propSchema: {
      ...defaultProps,
      cid: { default: '' },
      text: { default: '' },
      author: { default: '' },
      source: { default: '' },
    },
    content: 'none' as const,
  },
  {
    render: ({ block, editor }) => {
      const { text, author, source } = block.props;
      const updateProp = (key: string, next: string) => {
        editor.updateBlock(block, {
          props: { ...block.props, [key]: next },
        } as any);
      };
      const hasAttribution = author || source;
      return (
        <blockquote
          style={{
            borderLeft: '4px solid #000091',
            padding: '12px 18px',
            margin: '18px 0',
            background: '#f4f6fa',
            fontStyle: 'italic',
            color: '#1a1a1a',
            fontSize: 16,
            lineHeight: 1.6,
          }}
        >
          <InlineEditable
            as="div"
            value={String(text || '')}
            placeholder="Écrivez votre citation…"
            multiline
            ariaLabel="Modifier le texte de la citation"
            onChange={(next) => updateProp('text', next)}
            style={{ display: 'block' }}
          />
          <footer
            style={{
              marginTop: 8,
              fontSize: 13,
              color: '#666',
              fontStyle: 'normal',
              display: 'flex',
              gap: 8,
              flexWrap: 'wrap',
              alignItems: 'baseline',
            }}
          >
            <span aria-hidden="true">—</span>
            <InlineEditable
              as="span"
              value={String(author || '')}
              placeholder="Auteur…"
              ariaLabel="Modifier l'auteur"
              onChange={(next) => updateProp('author', next)}
              style={{ fontWeight: 500 }}
            />
            {(author || source) && <span aria-hidden="true">·</span>}
            <InlineEditable
              as="span"
              value={String(source || '')}
              placeholder="Source…"
              ariaLabel="Modifier la source"
              onChange={(next) => updateProp('source', next)}
              style={{ color: '#888' }}
            />
          </footer>
        </blockquote>
      );
    },
  },
);
