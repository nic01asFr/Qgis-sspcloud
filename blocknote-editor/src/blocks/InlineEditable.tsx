/**
 * InlineEditable — Composant d'edition inline pattern Docs LaSuite.
 *
 * Chantier 1 finalisation V1.20 (2026-07-06).
 *
 * Pattern : contentEditable sync sur blur/Enter -> editor.updateBlock({props}).
 * Adopte car BlockNote v0.22 `content: 'inline'` a echoue 3 fois historiquement
 * sur custom blocks (V1.11/V1.12.x rollback vers content: 'none' + drawer).
 *
 * Comportement Marie :
 * - Clique sur le texte -> curseur, edition directe (pas d'ouverture panel)
 * - Blur (clic ailleurs) ou Enter -> sauvegarde dans block.props via
 *   editor.updateBlock. L'autosave 30s BlockNote synchro le hub.
 * - Escape -> annule + restaure valeur initiale
 * - Placeholder si vide, affiche en gris italic
 *
 * Pattern testable sans BlockNote (juste ref DOM + callbacks).
 */
import { useEffect, useRef, useState } from 'react';
import { T } from '../design/tokens';

type InlineEditableProps = {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  multiline?: boolean;
  as?: 'span' | 'div' | 'h1' | 'h2' | 'h3' | 'h4' | 'p' | 'strong';
  style?: React.CSSProperties;
  className?: string;
  ariaLabel?: string;
  maxLength?: number;
  /** Si vrai : lecture seule (utile aperçu / DSFR strict) */
  readOnly?: boolean;
};

export function InlineEditable({
  value,
  onChange,
  placeholder = 'Cliquez pour écrire…',
  multiline = false,
  as: Tag = 'span',
  style,
  className,
  ariaLabel,
  maxLength,
  readOnly = false,
}: InlineEditableProps) {
  const ref = useRef<HTMLElement | null>(null);
  const [isFocused, setIsFocused] = useState(false);
  const [showPlaceholder, setShowPlaceholder] = useState(!value);

  // Sync value externe -> DOM (evite double render qui casse curseur)
  useEffect(() => {
    if (ref.current && ref.current.textContent !== value) {
      ref.current.textContent = value;
    }
    setShowPlaceholder(!value && !isFocused);
  }, [value, isFocused]);

  const handleBlur = () => {
    setIsFocused(false);
    const next = ref.current?.textContent ?? '';
    const trimmed = maxLength ? next.slice(0, maxLength) : next;
    if (trimmed !== value) {
      onChange(trimmed);
    }
    setShowPlaceholder(!trimmed);
  };

  const handleFocus = () => {
    setIsFocused(true);
    setShowPlaceholder(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      if (ref.current) ref.current.textContent = value;
      (ref.current as any)?.blur();
      return;
    }
    if (!multiline && e.key === 'Enter') {
      // Enter valide et blur (comme Docs)
      e.preventDefault();
      (ref.current as any)?.blur();
      return;
    }
    // Empeche propagation vers BlockNote pour Enter multiline (retour ligne local)
    if (multiline && e.key === 'Enter') {
      e.stopPropagation();
    }
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    // Coller en plain text (comme Docs) - fin des styles HTML colles
    e.preventDefault();
    const text = e.clipboardData.getData('text/plain');
    document.execCommand('insertText', false, text);
  };

  const editableStyle: React.CSSProperties = {
    outline: 'none',
    minWidth: '1ch',
    minHeight: multiline ? '1.5em' : undefined,
    borderRadius: T.radiusSm,
    padding: '2px 4px',
    margin: '-2px -4px',
    transition: `background ${T.transitionFast}, box-shadow ${T.transitionFast}`,
    cursor: readOnly ? 'default' : 'text',
    whiteSpace: multiline ? 'pre-wrap' : undefined,
    // Focus visible ring bleu Marianne
    ...(isFocused && {
      background: T.blueMarianneLight,
      boxShadow: `0 0 0 2px ${T.blueMarianne}22`,
    }),
    ...style,
    // Placeholder gris italic si vide et non focus
    ...(showPlaceholder && {
      color: T.textDisabled,
      fontStyle: 'italic',
    }),
  };

  return (
    <Tag
      ref={ref as any}
      contentEditable={!readOnly}
      suppressContentEditableWarning
      onFocus={handleFocus}
      onBlur={handleBlur}
      onKeyDown={handleKeyDown}
      onPaste={handlePaste}
      className={className}
      style={editableStyle}
      role="textbox"
      aria-label={ariaLabel || placeholder}
      aria-multiline={multiline || undefined}
      // Empeche BlockNote de traiter les clics/keydown pour lui
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {showPlaceholder ? placeholder : value}
    </Tag>
  );
}
