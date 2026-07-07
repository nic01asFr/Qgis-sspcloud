/**
 * AgentPanel — Panel Assistant redaction CEREMA V1.16.0 (2026-07-01).
 *
 * Sprint V1.16.0 quick wins Marie CEREMA (~4j, 13 items P0) :
 * - Design tokens DSFR CSS vars centralises (src/design/tokens.ts)
 * - Iconographie SVG inline (fin des emojis pour rendu pro admin + a11y)
 * - Retirer "IA" du header et onboarding (persona Marie mefiante-IA)
 * - Vouvoiement + verbes metier (vs tutoiement + jargon dev)
 * - Undo actionnable historique (clic ligne -> POST reversal_tool + reversal_args)
 * - Fin des alert() natifs : messages inline confirmation
 * - aria-live + aria-label + role=alert (RGAA CEREMA obligatoire NVDA)
 * - Couleurs DSFR strict (succes #18753c warning #b34000 erreur #ce0500)
 * - Focus-visible clavier + hover lift micro-interaction
 * - Skeleton shimmer pendant fetch (vs texte "Chargement...")
 * - Responsive Dell 13" via CSS clamp + auto-collapse <1280px
 * - Retirer fuite jargon "Iter 2 V2.5 SSE" -> zone desactivee sobre
 * - friendlyBasemap / Datasource / Field mappers slugs -> labels lisibles
 *
 * Version anterieure V1.15.0 : voir git history commit 84fa221.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { T, agentPanelCss, friendlyKind, friendlyBasemap } from './design/tokens';
import { EditPanel, type EditableBlock } from './EditPanel';
import { hubFetch } from './api/hubFetch';
import { ApiError, ConcurrentUpdateError } from './types/errors';
// Chantier 2 V1.20.2 : promptContext push depuis boutons "Assistant"
// contextuels dans les sections forms (useRequestAgentAssist).
import { usePromptContext, useEditorStore } from './store/editorStore';
import { useAssemblySuggestions } from './hooks/queries';

type Suggestion = {
  id: string;
  label: string;
  prompt: string;
  tool?: string;
  tool_args?: Record<string, any>;
  tool_args_partial?: Record<string, any>;
  hint?: string;
  requires_layer_selection?: boolean;
  requires_block_selection?: boolean;
};

type SelectedBlockContext = {
  block_id: string;
  type: string;
  props?: Record<string, any>;
} | null;

type ActionResult = {
  success: boolean;
  tool: string;
  action_type: string;
  cid?: string;
  aid?: string;
  block?: any;
  after_block_id?: string;
  component_version_num_after?: number;
  assembly_version_num_after?: number;
  history_entry?: {
    id: string;
    label: string;
    reversible: boolean;
    reversal_tool?: string;
    reversal_args?: Record<string, any>;
  };
};

type HistoryDisplay = {
  id: string;
  label: string;
  reversible: boolean;
  at: number;
  reversal_tool?: string;
  reversal_args?: Record<string, any>;
};

const LS_KEY_COLLAPSED = 'cerema.agent_panel.collapsed';
const LS_KEY_ONBOARDING_SEEN = 'cerema.agent_panel.first_seen';

/**
 * Injecte le CSS global une fois par window (idempotent).
 * Pattern adapte a l'existant : pas de CSS module, juste balise <style>.
 */
let cssInjected = false;
function useEnsureAgentPanelCss() {
  useLayoutEffect(() => {
    if (cssInjected) return;
    const el = document.createElement('style');
    el.setAttribute('data-cerema-agent-panel', '1');
    el.textContent = agentPanelCss;
    document.head.appendChild(el);
    cssInjected = true;
  }, []);
}

// ============================================================================
// Icones SVG inline (fin des emojis)
// ============================================================================

const iconStyle = { width: 16, height: 16, flexShrink: 0 } as const;

function IconLogo({ color = '#fff' }: { color?: string }) {
  // Assistant redaction : icone plume/edit sobre
  return (
    <svg viewBox="0 0 24 24" fill="none" style={{ ...iconStyle, width: 20, height: 20 }} aria-hidden="true">
      <path
        d="M4 20L15.5 8.5M15.5 8.5L18 6L20 8L17.5 10.5M15.5 8.5L17.5 10.5M6 20L4 20L4 18L15.5 6.5M17.5 10.5L6 22"
        stroke={color}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconChevronRight({ color }: { color?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" style={iconStyle} aria-hidden="true">
      <path d="M9 6l6 6-6 6" stroke={color || T.blueMarianne} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconChevronLeft({ color = '#fff' }: { color?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" style={iconStyle} aria-hidden="true">
      <path d="M15 6l-6 6 6 6" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconSelection({ color = T.blueMarianne }: { color?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" style={iconStyle} aria-hidden="true">
      <rect x="3" y="3" width="6" height="6" stroke={color} strokeWidth="1.6" />
      <rect x="15" y="15" width="6" height="6" stroke={color} strokeWidth="1.6" />
      <path d="M6 9v6M9 6h6M9 18h6M18 9v6" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeDasharray="1 3" />
    </svg>
  );
}

function IconCheck({ color = T.successFg }: { color?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" style={iconStyle} aria-hidden="true" className="cerema-check-svg">
      <path d="M5 12l5 5L20 7" stroke={color} strokeWidth="3" fill="none" />
    </svg>
  );
}

function IconUndo({ color = T.textMuted }: { color?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" style={iconStyle} aria-hidden="true">
      <path d="M9 14L4 9l5-5" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 9h11a5 5 0 015 5v3" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconAlert({ color = T.errorFg }: { color?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" style={iconStyle} aria-hidden="true">
      <path d="M12 3l10 18H2L12 3z" stroke={color} strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M12 10v4M12 17v.5" stroke={color} strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

// ============================================================================
// Composant principal
// ============================================================================

/**
 * Sprint V1.17 (2026-07-01) : AgentPanel devient BlockContextPanel unifie
 * avec 2 onglets Assistant / Parametres.
 *
 * Onglet Assistant (defaut) = comportement AgentPanel V1.16 (suggestions +
 * historique + undo + textarea disabled).
 *
 * Onglet Parametres = EditPanel rendu inline (form JSON per kind). Active
 * quand un bloc est selectionne. Ouvert automatiquement quand l'user clique
 * "Modifier" depuis un bloc custom (bridge window.__openEditPanel redirige
 * vers activeTab=parameters + selectedBlock).
 */
type PanelTab = 'assistant' | 'parameters';

export function AgentPanel({
  sid,
  aid,
  selectedBlock,
  editor,
  onActionApplied,
  onRequestReload,
  activeTab,
  onTabChange,
  editingBlock,
  versionNumSource,
  onEditPanelSaved,
  onEditPanelClose,
}: {
  sid: string;
  aid: string;
  selectedBlock: SelectedBlockContext;
  editor: any;
  onActionApplied?: (result: ActionResult) => void;
  onRequestReload?: () => void;
  // V1.17 : state onglet lifted au niveau App pour permettre bridge global
  // window.__openEditPanel -> setActiveTab('parameters') + setEditingBlock.
  activeTab: PanelTab;
  onTabChange: (tab: PanelTab) => void;
  editingBlock: EditableBlock | null;
  versionNumSource: number;
  onEditPanelSaved: (newProps: Record<string, any>) => void;
  onEditPanelClose: () => void;
}) {
  useEnsureAgentPanelCss();

  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(LS_KEY_COLLAPSED);
      if (stored !== null) return stored === '1';
      // Auto-collapse par defaut sur Dell 13" (Marie hardware CEREMA)
      return typeof window !== 'undefined' && window.innerWidth < T.responsiveBreakpoint;
    } catch {
      return false;
    }
  });
  const [firstSeen, setFirstSeen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(LS_KEY_ONBOARDING_SEEN) === '1';
    } catch {
      return true;
    }
  });
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [conflictErr, setConflictErr] = useState<boolean>(false);
  const [executing, setExecuting] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryDisplay[]>([]);
  const [freeInput, setFreeInput] = useState('');
  const debounceRef = useRef<number | null>(null);
  const onboardingBtnRef = useRef<HTMLButtonElement>(null);
  // Chantier 2 V1.20.2 : consommation promptContext push depuis les boutons
  // "Assistant" contextuels dans les sections forms. Le hint s'affiche dans
  // une banniere contextuelle (le textarea reste disabled tant que le chat
  // NL streaming SSE V2.5 n'est pas livre). Le user voit le contexte + peut
  // dismisser.
  const promptContext = usePromptContext();
  const clearPromptContext = useEditorStore((s) => s.setPromptContext);

  const toggleCollapse = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem(LS_KEY_COLLAPSED, next ? '1' : '0');
    } catch {}
  };

  const dismissOnboarding = () => {
    setFirstSeen(true);
    try {
      localStorage.setItem(LS_KEY_ONBOARDING_SEEN, '1');
    } catch {}
  };

  // Focus auto onboarding (RGAA 2.4.3 focus order)
  useEffect(() => {
    if (!firstSeen && !collapsed) {
      onboardingBtnRef.current?.focus();
    }
  }, [firstSeen, collapsed]);

  // Sprint V1.18 S3 (2026-07-05) : suggestions fetch migre vers TanStack Query
  // useAssemblySuggestions. Le hook fait AbortController + cache stale 30s +
  // rollback rejet automatique. Le debounce 250ms sur selection reste local
  // pour eviter le hammering du hub au deplacement rapide du curseur.
  const [debouncedSelectedBlockId, setDebouncedSelectedBlockId] = useState<
    string | null
  >(selectedBlock?.block_id ?? null);
  useEffect(() => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      setDebouncedSelectedBlockId(selectedBlock?.block_id ?? null);
    }, 250);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, [selectedBlock?.block_id]);

  const suggestionsQuery = useAssemblySuggestions(
    sid,
    aid,
    debouncedSelectedBlockId,
    { enabled: !collapsed },
  );

  // Sync data vers state local existant (evite refactor UI en aval)
  useEffect(() => {
    if (suggestionsQuery.data) {
      setSuggestions(suggestionsQuery.data.suggestions || []);
      setErrorMsg(null);
    } else if (suggestionsQuery.error) {
      setErrorMsg(String((suggestionsQuery.error as Error).message || suggestionsQuery.error));
    }
  }, [suggestionsQuery.data, suggestionsQuery.error]);

  const applyLiveUpdate = (result: ActionResult) => {
    try {
      if (result.action_type === 'component_updated' && result.cid) {
        const blockInDoc = editor.document.find(
          (b: any) => b.props?.cid === result.cid,
        );
        if (blockInDoc && result.component_version_num_after) {
          editor.updateBlock(blockInDoc, {
            props: {
              ...blockInDoc.props,
              version_num: result.component_version_num_after,
            },
          });
        }
      } else if (result.action_type === 'block_inserted' && result.block) {
        const newBlock = {
          type: mapKindToBlockNoteType(result.block.kind),
          props: {
            ...(result.block.params || {}),
            ...(result.block.component_ref
              ? { cid: result.block.component_ref.cid, sid }
              : {}),
          },
        };
        if (result.after_block_id) {
          const anchor = editor.document.find(
            (b: any) => b.id === result.after_block_id,
          );
          if (anchor) {
            editor.insertBlocks([newBlock], anchor, 'after');
          } else {
            editor.insertBlocks([newBlock], editor.document.slice(-1)[0], 'after');
          }
        }
      } else if (result.action_type === 'block_deleted') {
        const reversalArgs = result.history_entry?.reversal_args as any;
        const deletedId = reversalArgs?.block_id || reversalArgs?.after_block_id;
        if (deletedId) {
          const b = editor.document.find((x: any) => x.id === deletedId);
          if (b) editor.removeBlocks([b]);
        }
      }
    } catch (err) {
      console.warn('applyLiveUpdate failed', err);
    }
  };

  const postAction = async (
    tool: string,
    args: Record<string, any>,
    opts?: { cid?: string; executingId?: string },
  ): Promise<ActionResult | null> => {
    setExecuting(opts?.executingId || tool);
    setErrorMsg(null);
    setConflictErr(false);
    try {
      const isCmp = tool.startsWith('cmp_');
      const body: any = { tool, args };
      if (isCmp && (opts?.cid || selectedBlock?.props?.cid)) {
        body.cid = opts?.cid || selectedBlock?.props?.cid;
      }
      // Sprint V1.18 R3 : hubFetch hydrate erreurs typees (ApiError sous-classes).
      // silent=true car on gere l'affichage inline (setErrorMsg / setConflictErr).
      return await hubFetch<ActionResult>(
        `/studies/${sid}/assemblies/${aid}/assist/action`,
        { method: 'POST', json: body, silent: true },
      );
    } catch (err) {
      if (err instanceof ConcurrentUpdateError) {
        setConflictErr(true);
        return null;
      }
      if (err instanceof ApiError) {
        setErrorMsg(err.message);
        return null;
      }
      setErrorMsg(String(err));
      return null;
    } finally {
      setExecuting(null);
    }
  };

  const executeSuggestion = async (sug: Suggestion) => {
    if (!sid || !aid) return;
    if (sug.hint && !sug.tool) {
      // Escalade chat principal : message inline sobre (fin des alert())
      setErrorMsg(
        `Cette action necessite l'assistant complet. Ouvrez le chat CEREMA principal et demandez : "${sug.prompt}"`,
      );
      return;
    }
    if (
      (sug.requires_layer_selection || sug.requires_block_selection) &&
      !selectedBlock
    ) {
      setErrorMsg(
        "Cette action necessite de selectionner un element dans l'editeur au prealable.",
      );
      return;
    }
    const result = await postAction(sug.tool!, sug.tool_args || {}, {
      executingId: sug.id,
    });
    if (!result) return;
    applyLiveUpdate(result);
    if (result.history_entry) {
      setHistory((prev) => [
        {
          id: result.history_entry!.id,
          label: friendlyHistoryLabel(result.history_entry!.label),
          reversible: result.history_entry!.reversible,
          at: Date.now(),
          reversal_tool: result.history_entry!.reversal_tool,
          reversal_args: result.history_entry!.reversal_args,
        },
        ...prev.slice(0, 9),
      ]);
    }
    if (onActionApplied) onActionApplied(result);
  };

  // === UNDO actionnable (V1.16 item #1) ===
  const executeUndo = async (h: HistoryDisplay) => {
    if (!h.reversible || !h.reversal_tool) return;
    const result = await postAction(h.reversal_tool, h.reversal_args || {}, {
      executingId: `undo-${h.id}`,
    });
    if (!result) return;
    applyLiveUpdate(result);
    // Retire l'entree annulee et enregistre la nouvelle inversee
    setHistory((prev) => {
      const filtered = prev.filter((x) => x.id !== h.id);
      if (result.history_entry) {
        return [
          {
            id: result.history_entry.id,
            label: `Annule : ${h.label}`,
            reversible: false,
            at: Date.now(),
          },
          ...filtered.slice(0, 9),
        ];
      }
      return filtered;
    });
    if (onActionApplied) onActionApplied(result);
  };

  const handleReloadConflict = () => {
    setConflictErr(false);
    if (onRequestReload) {
      onRequestReload();
    } else {
      window.location.reload();
    }
  };

  // ==========================================================================
  // Render collapsed
  // ==========================================================================

  if (collapsed) {
    return (
      <aside
        className="cerema-panel collapsed"
        aria-label="Assistant redaction CEREMA (reduit)"
        style={{
          background: T.blueMarianne,
          borderLeft: `1px solid ${T.blueMarianneBorder}`,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: `${T.space3}px 0`,
          flexShrink: 0,
        }}
      >
        <button
          type="button"
          className="cerema-btn"
          onClick={toggleCollapse}
          aria-label="Ouvrir l'assistant redaction CEREMA"
          title="Ouvrir l'assistant redaction CEREMA"
          style={{
            background: 'transparent',
            border: 'none',
            color: T.white,
            cursor: 'pointer',
            padding: T.space2,
            borderRadius: T.radiusMd,
            width: 36,
            height: 36,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <IconLogo />
        </button>
        {history.length > 0 && (
          <span
            aria-label={`${history.length} action${history.length > 1 ? 's' : ''} enregistree${history.length > 1 ? 's' : ''}`}
            style={{
              width: 8,
              height: 8,
              borderRadius: 4,
              background: T.redMarianne,
              marginTop: T.space1,
            }}
          />
        )}
      </aside>
    );
  }

  // ==========================================================================
  // Render expanded
  // ==========================================================================

  return (
    <aside
      className="cerema-panel"
      role="complementary"
      aria-label="Assistant redaction CEREMA"
      style={{
        background: T.blueMarianneLight,
        borderLeft: `1px solid ${T.blueMarianneBorder}`,
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
        overflow: 'hidden',
        fontFamily: T.fontFamily,
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: `${T.space3}px ${T.space4}px`,
          background: T.blueMarianne,
          color: T.white,
          display: 'flex',
          alignItems: 'center',
          gap: T.space2,
        }}
      >
        <IconLogo />
        <div style={{ flex: 1, fontWeight: T.fontWeightBold, fontSize: T.fontSizeLg }}>
          Assistant redaction CEREMA
        </div>
        <button
          type="button"
          className="cerema-btn"
          onClick={toggleCollapse}
          aria-label="Reduire le panel"
          title="Reduire (raccourci : cliquer sur l'icone quand replie)"
          style={{
            background: 'transparent',
            border: 'none',
            color: T.white,
            cursor: 'pointer',
            padding: T.space1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: T.radiusSm,
          }}
        >
          <IconChevronLeft />
        </button>
      </div>

      {/* V1.17 : TabBar Assistant / Parametres */}
      <div
        role="tablist"
        aria-label="Selection du mode d'edition"
        style={{
          display: 'flex',
          background: T.white,
          borderBottom: `1px solid ${T.blueMarianneBorder}`,
        }}
      >
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'assistant'}
          className="cerema-btn"
          onClick={() => onTabChange('assistant')}
          style={{
            flex: 1,
            padding: `${T.space2}px ${T.space3}px`,
            background: 'transparent',
            border: 'none',
            borderBottom: `2px solid ${activeTab === 'assistant' ? T.blueMarianne : 'transparent'}`,
            color: activeTab === 'assistant' ? T.blueMarianne : T.textMuted,
            fontSize: T.fontSizeBase,
            fontWeight: activeTab === 'assistant' ? T.fontWeightBold : T.fontWeightRegular,
            cursor: 'pointer',
            fontFamily: 'inherit',
            transition: `border-color ${T.transitionFast}, color ${T.transitionFast}`,
          }}
        >
          Assistant
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'parameters'}
          className="cerema-btn"
          disabled={!editingBlock}
          onClick={() => onTabChange('parameters')}
          title={
            editingBlock
              ? 'Editer les parametres de l\'element selectionne'
              : 'Selectionnez un element du document pour editer ses parametres'
          }
          style={{
            flex: 1,
            padding: `${T.space2}px ${T.space3}px`,
            background: 'transparent',
            border: 'none',
            borderBottom: `2px solid ${activeTab === 'parameters' ? T.blueMarianne : 'transparent'}`,
            color: !editingBlock
              ? T.textDisabled
              : activeTab === 'parameters'
                ? T.blueMarianne
                : T.textMuted,
            fontSize: T.fontSizeBase,
            fontWeight: activeTab === 'parameters' ? T.fontWeightBold : T.fontWeightRegular,
            cursor: !editingBlock ? 'not-allowed' : 'pointer',
            fontFamily: 'inherit',
            transition: `border-color ${T.transitionFast}, color ${T.transitionFast}`,
          }}
        >
          Parametres
        </button>
      </div>

      {/* V1.17 : Onglet Parametres = EditPanel inline */}
      {activeTab === 'parameters' && editingBlock && (
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <EditPanel
            block={editingBlock}
            sid={sid}
            versionNumSource={versionNumSource}
            onSaved={onEditPanelSaved}
            onClose={onEditPanelClose}
            mode="inline"
          />
        </div>
      )}

      {activeTab === 'parameters' && !editingBlock && (
        <div
          style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: T.space4,
            fontSize: T.fontSizeBase,
            color: T.textMuted,
            fontStyle: 'italic',
            textAlign: 'center',
          }}
        >
          Selectionnez un element du document pour editer ses parametres.
        </div>
      )}

      {/* Onboarding tooltip (first-visit) — onglet Assistant uniquement */}
      {activeTab === 'assistant' && !firstSeen && (
        <div
          role="region"
          aria-label="Message d'accueil"
          style={{
            padding: T.space3,
            background: T.warningBg,
            borderBottom: `1px solid ${T.warningFg}44`,
            fontSize: T.fontSizeBase,
            color: T.warningFg,
            lineHeight: 1.5,
          }}
        >
          <strong style={{ display: 'block', marginBottom: T.space1 }}>
            Bienvenue.
          </strong>
          Selectionnez un element du document (carte, titre, paragraphe) : les
          actions utiles s'affichent ici. Vous pouvez aussi decrire ce que vous
          voulez ajouter, en francais.
          <div style={{ marginTop: T.space2 }}>
            <button
              ref={onboardingBtnRef}
              type="button"
              className="cerema-btn"
              onClick={dismissOnboarding}
              style={{
                padding: `${T.space1}px ${T.space3}px`,
                background: T.warningFg,
                color: T.white,
                border: 'none',
                borderRadius: T.radiusMd,
                cursor: 'pointer',
                fontSize: T.fontSizeSm,
                fontFamily: 'inherit',
                fontWeight: T.fontWeightMedium,
              }}
            >
              Compris
            </button>
          </div>
        </div>
      )}

      {/* Contexte selection (onglet Assistant uniquement) */}
      {activeTab === 'assistant' && (
      <div
        style={{
          padding: `${T.space2}px ${T.space4}px`,
          background: T.white,
          borderBottom: `1px solid ${T.blueMarianneBorder}`,
          fontSize: T.fontSizeBase,
        }}
        aria-live="polite"
      >
        {selectedBlock ? (
          <>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: T.space2,
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ color: T.textMuted, fontSize: T.fontSizeSm }}>
                  Element selectionne :
                </div>
                <div
                  style={{
                    fontWeight: T.fontWeightBold,
                    color: T.blueMarianne,
                    display: 'flex',
                    alignItems: 'center',
                    gap: T.space1,
                    marginTop: 2,
                  }}
                >
                  <IconSelection />
                  {friendlyKind(selectedBlock.type)}
                </div>
              </div>
              {editingBlock && (
                <button
                  type="button"
                  className="cerema-btn cerema-action"
                  onClick={() => onTabChange('parameters')}
                  aria-label="Editer les parametres de cet element"
                  title="Editer les parametres"
                  style={{
                    padding: `${T.space1}px ${T.space2}px`,
                    background: T.white,
                    color: T.blueMarianne,
                    border: `1px solid ${T.blueMarianne}`,
                    borderRadius: T.radiusMd,
                    fontSize: T.fontSizeSm,
                    fontWeight: T.fontWeightMedium,
                    cursor: 'pointer',
                    fontFamily: 'inherit',
                    whiteSpace: 'nowrap',
                  }}
                >
                  Parametres
                </button>
              )}
            </div>
          </>
        ) : (
          <div style={{ color: T.textMuted, fontStyle: 'italic' }}>
            Aucun element selectionne — actions generales du livrable ci-dessous.
          </div>
        )}
      </div>
      )}

      {/* Body scrollable (onglet Assistant uniquement) */}
      {activeTab === 'assistant' && (
      <div
        className="cerema-panel-content"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: `${T.space3}px ${T.space4}px`,
        }}
      >
        {/* Chantier 2 V1.20.2 : banniere contexte prompt push depuis
            un bouton "Assistant" contextuel dans une section forms.
            Marie voit d'ou vient la demande + peut dismisser. */}
        {promptContext && (
          <div
            role="status"
            style={{
              padding: T.space2,
              marginBottom: T.space3,
              background: T.blueMarianneLight,
              border: `1px solid ${T.blueMarianne}33`,
              borderRadius: T.radiusMd,
              fontSize: T.fontSizeSm,
              color: T.blueMarianne,
              display: 'flex',
              alignItems: 'flex-start',
              gap: T.space2,
            }}
          >
            <span aria-hidden="true">⚡</span>
            <div style={{ flex: 1, lineHeight: 1.5 }}>
              <div>
                Demande contextuelle :{' '}
                <strong>{friendlyKind(promptContext.kind || '')}</strong>
                {promptContext.section && (
                  <>
                    {' '}· section <em>{promptContext.section}</em>
                  </>
                )}
              </div>
              {promptContext.hint && (
                <div
                  style={{
                    marginTop: T.space1,
                    fontSize: T.fontSizeSm,
                    color: T.textSecondary,
                    fontStyle: 'italic',
                  }}
                >
                  « {promptContext.hint} »
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => clearPromptContext(null)}
              aria-label="Retirer le contexte"
              title="Retirer le contexte"
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: T.blueMarianne,
                padding: 0,
                lineHeight: 1,
                fontSize: T.fontSizeMd,
              }}
            >
              ×
            </button>
          </div>
        )}
        {/* Conflit 409 : bandeau inline (fin des alert()) */}
        {conflictErr && (
          <div
            role="alert"
            style={{
              padding: T.space3,
              background: T.warningBg,
              border: `1px solid ${T.warningFg}44`,
              borderRadius: T.radiusMd,
              fontSize: T.fontSizeBase,
              color: T.warningFg,
              marginBottom: T.space3,
              display: 'flex',
              flexDirection: 'column',
              gap: T.space2,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: T.space2 }}>
              <IconAlert color={T.warningFg} />
              <span>
                Ce document a ete modifie ailleurs (autre onglet ou collegue).
                Rechargez la page pour repartir de la derniere version.
              </span>
            </div>
            <div style={{ display: 'flex', gap: T.space2 }}>
              <button
                type="button"
                className="cerema-btn"
                onClick={handleReloadConflict}
                style={{
                  padding: `${T.space1}px ${T.space3}px`,
                  background: T.warningFg,
                  color: T.white,
                  border: 'none',
                  borderRadius: T.radiusMd,
                  cursor: 'pointer',
                  fontSize: T.fontSizeSm,
                  fontFamily: 'inherit',
                  fontWeight: T.fontWeightMedium,
                }}
              >
                Recharger la page
              </button>
              <button
                type="button"
                className="cerema-btn"
                onClick={() => setConflictErr(false)}
                style={{
                  padding: `${T.space1}px ${T.space3}px`,
                  background: 'transparent',
                  color: T.warningFg,
                  border: `1px solid ${T.warningFg}`,
                  borderRadius: T.radiusMd,
                  cursor: 'pointer',
                  fontSize: T.fontSizeSm,
                  fontFamily: 'inherit',
                }}
              >
                Ignorer
              </button>
            </div>
          </div>
        )}

        {/* Erreur generique : role=alert (aria-live) */}
        {errorMsg && !conflictErr && (
          <div
            role="alert"
            style={{
              padding: T.space3,
              background: T.errorBg,
              border: `1px solid ${T.errorFg}44`,
              borderRadius: T.radiusMd,
              fontSize: T.fontSizeBase,
              color: T.errorFg,
              marginBottom: T.space3,
              display: 'flex',
              alignItems: 'flex-start',
              gap: T.space2,
            }}
          >
            <IconAlert />
            <div style={{ flex: 1 }}>{errorMsg}</div>
            <button
              type="button"
              onClick={() => setErrorMsg(null)}
              aria-label="Fermer le message d'erreur"
              style={{
                background: 'transparent',
                border: 'none',
                color: T.errorFg,
                cursor: 'pointer',
                padding: 0,
                fontSize: T.fontSizeLg,
                lineHeight: 1,
              }}
            >
              ×
            </button>
          </div>
        )}

        {/* Skeleton loader */}
        {suggestions === null && !errorMsg && (
          <div aria-busy="true" aria-label="Chargement des suggestions">
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="cerema-skel"
                style={{ height: 32, marginBottom: T.space2 }}
              />
            ))}
          </div>
        )}

        {/* Actions rapides */}
        {suggestions && suggestions.length > 0 && (
          <div
            role="region"
            aria-live="polite"
            aria-label="Actions rapides disponibles"
            style={{ marginBottom: T.space4 }}
          >
            <div
              style={{
                fontSize: T.fontSizeSm,
                fontWeight: T.fontWeightBold,
                color: T.textSecondary,
                textTransform: 'uppercase',
                marginBottom: T.space2,
                letterSpacing: T.letterSpacingWide,
              }}
            >
              Actions rapides
            </div>
            {suggestions.map((sug) => {
              const isExec = executing === sug.id;
              return (
                <button
                  key={sug.id}
                  type="button"
                  className="cerema-btn cerema-action"
                  disabled={!!executing}
                  onClick={() => executeSuggestion(sug)}
                  aria-label={sug.label + (sug.hint ? ' (necessite assistant complet)' : '')}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: T.space2,
                    padding: `${T.space2}px ${T.space3}px`,
                    marginBottom: T.space1 + 2,
                    width: '100%',
                    background: isExec ? T.blueMarianne : T.white,
                    color: isExec ? T.white : T.textPrimary,
                    border: `1px solid ${T.borderDefault}`,
                    borderRadius: T.radiusMd,
                    fontSize: T.fontSizeBase,
                    cursor: executing ? 'wait' : 'pointer',
                    fontFamily: 'inherit',
                    textAlign: 'left' as const,
                    lineHeight: 1.4,
                  }}
                >
                  <IconChevronRight color={isExec ? T.white : T.blueMarianne} />
                  <span style={{ flex: 1 }}>{sug.label}</span>
                  {sug.hint && (
                    <span
                      title="Cette action necessite l'assistant complet (chat principal)"
                      style={{
                        fontSize: T.fontSizeXs,
                        color: isExec ? T.white : T.textMuted,
                        fontStyle: 'italic',
                      }}
                    >
                      →chat
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}

        {/* Zone chat NL — desactivee jusqu'a livraison iter 2 (fin jargon dev) */}
        <div
          style={{
            paddingTop: T.space3,
            borderTop: `1px solid ${T.blueMarianneBorder}`,
            marginBottom: T.space3,
          }}
        >
          <div
            style={{
              fontSize: T.fontSizeSm,
              fontWeight: T.fontWeightBold,
              color: T.textSecondary,
              textTransform: 'uppercase',
              marginBottom: T.space1 + 2,
              letterSpacing: T.letterSpacingWide,
            }}
          >
            Ou decrivez votre besoin
          </div>
          <textarea
            value={freeInput}
            onChange={(e) => setFreeInput(e.target.value)}
            rows={3}
            disabled
            aria-label="Zone de description en francais (activation prochaine)"
            placeholder="Ex : Ajouter une carte de Marseille 4e avec le perimetre TRI inondation, ou un chiffre cle : 12 000 logements exposes."
            style={{
              width: '100%',
              padding: T.space2,
              fontSize: T.fontSizeBase,
              border: `1px solid ${T.borderDefault}`,
              borderRadius: T.radiusMd,
              fontFamily: 'inherit',
              resize: 'vertical' as const,
              boxSizing: 'border-box' as const,
              background: T.bgAlt,
              color: T.textMuted,
              cursor: 'not-allowed',
            }}
          />
          <div
            style={{
              fontSize: T.fontSizeXs,
              color: T.textMuted,
              marginTop: T.space1,
              fontStyle: 'italic',
            }}
          >
            Zone activee prochainement. En attendant : utilisez les actions
            rapides ci-dessus, ou l'assistant complet accessible depuis le
            bandeau superieur.
          </div>
        </div>

        {/* Historique + Undo actionnable */}
        {history.length > 0 && (
          <div
            role="log"
            aria-live="polite"
            aria-label="Historique des actions realisees"
            style={{
              paddingTop: T.space3,
              borderTop: `1px solid ${T.blueMarianneBorder}`,
            }}
          >
            <div
              style={{
                fontSize: T.fontSizeSm,
                fontWeight: T.fontWeightBold,
                color: T.textSecondary,
                textTransform: 'uppercase',
                marginBottom: T.space1 + 2,
                letterSpacing: T.letterSpacingWide,
              }}
            >
              Historique ({history.length})
            </div>
            {history.map((h) => (
              <div
                key={h.id}
                className="cerema-history-item"
                style={{
                  padding: `${T.space1 + 2}px ${T.space2}px`,
                  marginBottom: T.space1,
                  background: T.white,
                  borderRadius: T.radiusMd,
                  fontSize: T.fontSizeSm,
                  display: 'flex',
                  alignItems: 'center',
                  gap: T.space1 + 2,
                }}
              >
                <IconCheck />
                <span
                  style={{
                    flex: 1,
                    color: T.textSecondary,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                  title={h.label}
                >
                  {h.label}
                </span>
                <span style={{ color: T.textDisabled, fontSize: T.fontSizeXs }}>
                  {timeSince(h.at)}
                </span>
                {h.reversible && h.reversal_tool && (
                  <button
                    type="button"
                    className="cerema-btn cerema-undo-btn"
                    onClick={() => executeUndo(h)}
                    disabled={!!executing}
                    aria-label={`Annuler : ${h.label}`}
                    title="Annuler cette action"
                    style={{
                      background: 'transparent',
                      border: 'none',
                      cursor: executing ? 'wait' : 'pointer',
                      padding: 2,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      borderRadius: T.radiusSm,
                    }}
                  >
                    <IconUndo />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Empty state historique : discret pedagogique */}
        {history.length === 0 && suggestions && suggestions.length > 0 && !executing && (
          <div
            style={{
              paddingTop: T.space3,
              borderTop: `1px solid ${T.blueMarianneBorder}`,
              fontSize: T.fontSizeXs,
              color: T.textMuted,
              fontStyle: 'italic',
              lineHeight: 1.5,
            }}
          >
            Chaque action realisee ici apparaitra dans un journal reversible en
            un clic.
          </div>
        )}
      </div>
      )}
    </aside>
  );
}

// ============================================================================
// Helpers
// ============================================================================

function mapKindToBlockNoteType(kind: string): string {
  const map: Record<string, string> = {
    interactive_map: 'interactiveMap',
    kpi_grid: 'kpiGrid',
    kpi_badge: 'kpiBadge',
    heading: 'customHeading',
    quote: 'customQuote',
    narrative_text: 'narrativeText',
    legend: 'legend',
    separator: 'separator',
    chart: 'chart',
    scene_3d: 'scene3d',
    data_table: 'dataTable',
    media_embed: 'mediaEmbed',
    iframe_grist: 'iframeGrist',
  };
  return map[kind] || kind;
}

function timeSince(at: number): string {
  const s = Math.floor((Date.now() - at) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}min`;
  return `${Math.floor(m / 60)}h`;
}

/**
 * Rend les labels historique plus lisibles pour Marie :
 * "Fond de carte : plan-ign-v2" -> "Fond de carte : Plan IGN (standard)".
 * Iter 1 : parse simple. Iter 2 : structuration cote backend HistoryEntry.
 */
function friendlyHistoryLabel(rawLabel: string): string {
  // Cas connu : "Fond de carte : <slug>"
  const basemapMatch = rawLabel.match(/^Fond de carte\s*:\s*(.+)$/i);
  if (basemapMatch) {
    return `Fond de carte : ${friendlyBasemap(basemapMatch[1].trim())}`;
  }
  return rawLabel;
}
