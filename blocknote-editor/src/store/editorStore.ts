/**
 * editorStore — Zustand store centralise UI editeur qgis-sspcloud.
 *
 * Sprint V2.0 Phase 1 Etape 3 - R1 + S8 (2026-07-05).
 *
 * Remplace :
 * - window.__openEditPanel (bridge global V1.10 anti-pattern React)
 * - window.__switchPanelTab (bridge global V1.17)
 * - state lifted App.tsx (panelActiveTab, editingBlock, selectedBlock)
 *
 * Pattern : Zustand slices co-localisees + selecteurs memorises. Testable
 * unitairement (import { useEditorStore } - pas de <Provider> requis).
 * StrictMode-safe (pas de double-init bridges globaux).
 *
 * Backward compat V1.17.2 : les bridges window.__* sont conserves en shim
 * dans App.tsx pour eviter de casser les custom blocks non encore migres
 * (blocks/edit-handler.ts). Ces shims appellent simplement les setters
 * Zustand. Migration blocks/*.tsx a useEditorStore = V1.18+.
 */
import { create } from 'zustand';

export type PanelTab = 'assistant' | 'parameters';

export interface EditableBlock {
  id: string;
  type: string;
  props: Record<string, any>;
  anchorRect?: DOMRect;
}

export interface SelectedBlockContext {
  block_id: string;
  type: string;
  props?: Record<string, any>;
}

interface EditorState {
  // === State fluctuant UI panel ===
  panelActiveTab: PanelTab;
  editingBlock: EditableBlock | null;
  selectedBlock: SelectedBlockContext | null;

  // === Actions ===
  setPanelActiveTab: (tab: PanelTab) => void;
  setEditingBlock: (block: EditableBlock | null) => void;
  setSelectedBlock: (ctx: SelectedBlockContext | null) => void;

  /**
   * Ouvre l'onglet Parametres avec le bloc a editer.
   * Utilise par les custom blocks (bouton "Modifier") via bridge
   * window.__openEditPanel (shim App.tsx) ou directement via
   * useEditorStore().openEditPanel(block).
   */
  openEditPanel: (block: EditableBlock) => void;

  /**
   * Bascule programmatique d'onglet (utilise par LayersFieldset
   * bouton "+ Ajouter une couche" pour retourner sur Assistant).
   */
  switchPanelTab: (tab: PanelTab) => void;

  /**
   * Ferme l'onglet Parametres et revient sur Assistant.
   * Utilise par onEditPanelClose et onEditPanelSaved callbacks
   * dans EditPanel inline.
   */
  closeEditPanel: () => void;
}

export const useEditorStore = create<EditorState>((set) => ({
  panelActiveTab: 'assistant',
  editingBlock: null,
  selectedBlock: null,

  setPanelActiveTab: (tab) => set({ panelActiveTab: tab }),
  setEditingBlock: (block) => set({ editingBlock: block }),
  setSelectedBlock: (ctx) => set({ selectedBlock: ctx }),

  openEditPanel: (block) =>
    set({ editingBlock: block, panelActiveTab: 'parameters' }),

  switchPanelTab: (tab) => set({ panelActiveTab: tab }),

  closeEditPanel: () =>
    set({ editingBlock: null, panelActiveTab: 'assistant' }),
}));

/**
 * Selecteurs pretes a l'emploi (evite passer selecteurs a chaque useEditorStore
 * call, permet subscription fine-grained pour eviter re-renders).
 *
 * Usage : const editingBlock = useEditingBlock();
 */
export const usePanelActiveTab = () => useEditorStore((s) => s.panelActiveTab);
export const useEditingBlock = () => useEditorStore((s) => s.editingBlock);
export const useSelectedBlock = () => useEditorStore((s) => s.selectedBlock);
