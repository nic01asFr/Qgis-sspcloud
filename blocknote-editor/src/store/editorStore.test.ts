/**
 * Tests unitaires editorStore Zustand.
 *
 * Sprint V2.0 Phase 1 Etape 3 - R1 + S8.
 *
 * Executable via vitest ou jest. Le harness CI n'est pas encore branche
 * (Sprint 1.1 Etape 4 - S2 activera). Ces tests documentent le contract
 * du store et permettent de detecter regression lors des futures migrations
 * blocks/*.tsx -> useEditorStore.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useEditorStore } from './editorStore';

describe('editorStore', () => {
  beforeEach(() => {
    // Reset store entre tests
    useEditorStore.setState({
      panelActiveTab: 'assistant',
      editingBlock: null,
      selectedBlock: null,
    });
  });

  it('defaut : onglet Assistant, no editingBlock, no selectedBlock', () => {
    const s = useEditorStore.getState();
    expect(s.panelActiveTab).toBe('assistant');
    expect(s.editingBlock).toBeNull();
    expect(s.selectedBlock).toBeNull();
  });

  it('openEditPanel(block) : bascule sur Parametres + set editingBlock', () => {
    const block = { id: 'b1', type: 'heading', props: { level: 2 } };
    useEditorStore.getState().openEditPanel(block);
    const s = useEditorStore.getState();
    expect(s.editingBlock).toEqual(block);
    expect(s.panelActiveTab).toBe('parameters');
  });

  it('closeEditPanel() : clear editingBlock + retour Assistant', () => {
    useEditorStore.setState({
      panelActiveTab: 'parameters',
      editingBlock: { id: 'b1', type: 'heading', props: {} },
    });
    useEditorStore.getState().closeEditPanel();
    const s = useEditorStore.getState();
    expect(s.editingBlock).toBeNull();
    expect(s.panelActiveTab).toBe('assistant');
  });

  it('switchPanelTab(tab) : bascule sans toucher editingBlock', () => {
    const block = { id: 'b1', type: 'heading', props: {} };
    useEditorStore.setState({
      panelActiveTab: 'assistant',
      editingBlock: block,
    });
    useEditorStore.getState().switchPanelTab('parameters');
    const s = useEditorStore.getState();
    expect(s.panelActiveTab).toBe('parameters');
    expect(s.editingBlock).toEqual(block);
  });

  it('setSelectedBlock(ctx) : met a jour contexte selection', () => {
    const ctx = { block_id: 'b2', type: 'interactiveMap', props: { cid: 'c1' } };
    useEditorStore.getState().setSelectedBlock(ctx);
    expect(useEditorStore.getState().selectedBlock).toEqual(ctx);
  });

  it('setSelectedBlock(null) : clear', () => {
    useEditorStore.setState({
      selectedBlock: { block_id: 'b1', type: 'heading' },
    });
    useEditorStore.getState().setSelectedBlock(null);
    expect(useEditorStore.getState().selectedBlock).toBeNull();
  });
});
