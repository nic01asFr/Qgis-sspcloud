/**
 * Helper partagé : click handler pour les custom blocks DOM.
 *
 * Sprint 4 v1.10.0 (8.18) : click sur un block custom → ouvre EditPanel drawer.
 * Communication via global window.__openEditPanel (bridge App.tsx <-> blocks).
 *
 * Inclus aussi un style "hover hint" pour signaler à Marie que le block
 * est cliquable (border bleu Marianne au hover).
 */

export interface ClickableBlockProps {
  block: { id: string; type: string; props: Record<string, any> };
}

export function openEditPanel(
  block: ClickableBlockProps['block'],
  event?: { currentTarget?: HTMLElement } | MouseEvent,
) {
  const fn = (window as any).__openEditPanel;
  if (typeof fn === 'function') {
    // v1.11 Phase B : capturer le rect du block pour positionner le popup
    let anchorRect: DOMRect | undefined;
    const target =
      (event as any)?.currentTarget ||
      document.querySelector(`[data-id="${block.id}"]`);
    if (target && typeof target.getBoundingClientRect === 'function') {
      anchorRect = target.getBoundingClientRect();
    }
    fn({
      id: block.id,
      type: block.type,
      props: block.props,
      anchorRect,
    });
  } else {
    console.warn('window.__openEditPanel not registered');
  }
}

/**
 * Style overlay "Cliquer pour modifier" affiché au hover.
 * Wrapper div autour du contenu rendu du block.
 */
export const editableWrapperStyle: React.CSSProperties = {
  position: 'relative',
  cursor: 'pointer',
  borderRadius: 4,
  transition: 'box-shadow 0.15s, outline 0.15s',
};

export const editableWrapperHoverClass = 'qgis-editable-block';
