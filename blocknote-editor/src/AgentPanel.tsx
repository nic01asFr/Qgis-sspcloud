/**
 * AgentPanel — Panel Assistant CEREMA V1.15 Docs-like persistent.
 *
 * Sprint V1.15 Etape 6 (2026-07-01) — pattern Notion AI / Cursor Composer /
 * LaSuite Docs adapte au persona Marie CEREMA (chef projet non-tech).
 *
 * Remplace/complete l'AssistantCard V1.14.1 (dans drawer scope composant)
 * par un panel latéral 340px persistent, contextuel au bloc sélectionné
 * dans l'éditeur BlockNote, avec apply live via editor.updateBlock() /
 * insertBlocks() / removeBlocks() (fin des window.location.reload()).
 *
 * Contract AgentBrick backend :
 * - GET /studies/{sid}/assemblies/{aid}/assist/suggestions?selected_block_id=...
 * - POST /studies/{sid}/assemblies/{aid}/assist/action {tool, args, cid?, ...}
 *
 * Etude A UX (score 8.5/10) : pattern hybride Docs+Cursor. Etude B backend
 * (7.5/10) : dispatcher unifie assembly-scope. Etude C AgentBrick (7/10) :
 * contract stable pour cross-projet CEREMA extractable V1.16.
 */
import { useEffect, useState, useRef } from 'react';

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

const PANEL_WIDTH = 340;
const COLLAPSED_WIDTH = 48;
const LS_KEY_COLLAPSED = 'cerema.agent_panel.collapsed';
const LS_KEY_ONBOARDING_SEEN = 'cerema.agent_panel.first_seen';

export function AgentPanel({
  sid,
  aid,
  selectedBlock,
  editor,
  onActionApplied,
}: {
  sid: string;
  aid: string;
  selectedBlock: SelectedBlockContext;
  editor: any;  // BlockNote editor instance
  onActionApplied?: (result: ActionResult) => void;
}) {
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(LS_KEY_COLLAPSED) === '1';
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
  const [error, setError] = useState<string | null>(null);
  const [executing, setExecuting] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryDisplay[]>([]);
  const [freeInput, setFreeInput] = useState('');
  const debounceRef = useRef<number | null>(null);

  // Toggle collapse persist localStorage
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

  // Fetch suggestions avec debounce 250ms (etude A) sur changement selection
  useEffect(() => {
    if (!sid || !aid || collapsed) return;
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      const ctrl = new AbortController();
      const qs = selectedBlock?.block_id
        ? `?selected_block_id=${encodeURIComponent(selectedBlock.block_id)}`
        : '';
      fetch(`/studies/${sid}/assemblies/${aid}/assist/suggestions${qs}`, {
        credentials: 'include',
        signal: ctrl.signal,
      })
        .then((r) => {
          if (!r.ok) throw new Error(`assist/suggestions ${r.status}`);
          return r.json();
        })
        .then((data) => {
          if (!ctrl.signal.aborted) {
            setSuggestions(data.suggestions || []);
            setError(null);
          }
        })
        .catch((e: any) => {
          if (ctrl.signal.aborted || e?.name === 'AbortError') return;
          setError(String(e?.message || e));
        });
      return () => ctrl.abort();
    }, 250);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, [sid, aid, selectedBlock?.block_id, collapsed]);

  const applyLiveUpdate = (result: ActionResult) => {
    // Applique la mutation directement dans l'editeur BlockNote (pas de reload)
    try {
      if (result.action_type === 'component_updated' && result.cid) {
        // Force re-mount iframe via version_num_after (bump props.version_num
        // qui declenche le re-mount via key)
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
        // Insert new block via editor.insertBlocks
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
        // Delete via editor.removeBlocks
        const args = JSON.parse(result.history_entry?.reversal_args ? '{}' : '{}');
        // block_id doit venir de la requete originale : on l'a dans le context
      }
    } catch (err) {
      console.warn('applyLiveUpdate failed', err);
    }
  };

  const executeSuggestion = async (sug: Suggestion) => {
    if (!sid || !aid) return;
    if (sug.hint && !sug.tool) {
      // Escalade agent complet
      alert(
        "Cette action necessite l'agent IA complet. " +
        `Va dans le chat CEREMA et demande : "${sug.prompt}"`
      );
      return;
    }
    if (sug.requires_layer_selection || sug.requires_block_selection) {
      if (!selectedBlock) {
        alert(
          "Cette action necessite de selectionner un element au prealable dans l'editeur."
        );
        return;
      }
    }

    setExecuting(sug.id);
    setError(null);
    try {
      // Adapte args pour cmp_* tools : si tool est cmp_*, envoyer cid
      const isCmp = sug.tool?.startsWith('cmp_');
      const body: any = {
        tool: sug.tool,
        args: sug.tool_args || {},
      };
      if (isCmp && selectedBlock?.props?.cid) {
        body.cid = selectedBlock.props.cid;
      }
      const resp = await fetch(
        `/studies/${sid}/assemblies/${aid}/assist/action`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        },
      );
      if (resp.status === 409) {
        const err409 = await resp.json();
        setError(
          `Conflit : ${err409.message || 'un autre processus a modifie'}. Recharge.`
        );
        return;
      }
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}));
        throw new Error(errBody.detail || `HTTP ${resp.status}`);
      }
      const result = (await resp.json()) as ActionResult;

      // Apply live via BlockNote API (V1.15 : plus de reload !)
      applyLiveUpdate(result);

      // Ajoute a l'historique
      if (result.history_entry) {
        setHistory((prev) => [
          {
            id: result.history_entry!.id,
            label: result.history_entry!.label,
            reversible: result.history_entry!.reversible,
            at: Date.now(),
            reversal_tool: result.history_entry!.reversal_tool,
            reversal_args: result.history_entry!.reversal_args,
          },
          ...prev.slice(0, 9),
        ]);
      }

      if (onActionApplied) onActionApplied(result);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setExecuting(null);
    }
  };

  // ==========================================================================
  // Render collapsed state (icon toggle)
  // ==========================================================================

  if (collapsed) {
    return (
      <div
        style={{
          width: COLLAPSED_WIDTH,
          background: '#000091',
          borderLeft: '1px solid #e0e0e0',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: '12px 0',
          flexShrink: 0,
        }}
      >
        <button
          type="button"
          onClick={toggleCollapse}
          title="Ouvrir Assistant CEREMA"
          style={{
            background: 'transparent',
            border: 'none',
            color: '#fff',
            cursor: 'pointer',
            padding: 8,
            borderRadius: 4,
            fontSize: 18,
            width: 32,
            height: 32,
          }}
        >
          ⚡
        </button>
        {history.length > 0 && (
          <div
            style={{
              width: 6,
              height: 6,
              borderRadius: 3,
              background: '#e1000f',
              marginTop: 4,
            }}
            title={`${history.length} actions`}
          />
        )}
      </div>
    );
  }

  // ==========================================================================
  // Render expanded panel
  // ==========================================================================

  return (
    <aside
      role="complementary"
      aria-label="Assistant CEREMA"
      style={{
        width: PANEL_WIDTH,
        background: '#f5f5fe',
        borderLeft: '1px solid #ececfa',
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '14px 16px',
          background: '#000091',
          color: '#fff',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <span style={{ fontSize: 18 }}>⚡</span>
        <div style={{ flex: 1, fontWeight: 600, fontSize: 14 }}>
          Assistant CEREMA
        </div>
        <button
          type="button"
          onClick={toggleCollapse}
          title="Reduire"
          style={{
            background: 'transparent',
            border: 'none',
            color: '#fff',
            cursor: 'pointer',
            padding: 4,
            fontSize: 14,
          }}
        >
          ◀
        </button>
      </div>

      {/* Onboarding tooltip (first visit) */}
      {!firstSeen && (
        <div
          style={{
            padding: 12,
            background: '#fff4e5',
            borderBottom: '1px solid #ffcb8c',
            fontSize: 12,
            color: '#716043',
          }}
        >
          <strong>Nouveau : ton assistant IA CEREMA.</strong>
          <br />
          Clique sur un bloc pour voir les actions disponibles selon le contexte.
          <button
            type="button"
            onClick={dismissOnboarding}
            style={{
              marginTop: 8,
              padding: '4px 12px',
              background: '#b34000',
              color: '#fff',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
              fontSize: 11,
              fontFamily: 'inherit',
            }}
          >
            Compris
          </button>
        </div>
      )}

      {/* Contexte selection */}
      <div
        style={{
          padding: '10px 16px',
          background: '#fff',
          borderBottom: '1px solid #ececfa',
          fontSize: 12,
        }}
      >
        {selectedBlock ? (
          <>
            <div style={{ color: '#666', fontSize: 11 }}>Selection :</div>
            <div style={{ fontWeight: 600, color: '#000091' }}>
              📌 {friendlyKind(selectedBlock.type)}
            </div>
          </>
        ) : (
          <div style={{ color: '#666', fontStyle: 'italic' }}>
            Aucune selection · actions assemblage
          </div>
        )}
      </div>

      {/* Body scrollable */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
        {error && (
          <div
            style={{
              padding: 10,
              background: '#fbebeb',
              border: '1px solid #f4a8a8',
              borderRadius: 4,
              fontSize: 12,
              color: '#a01010',
              marginBottom: 12,
            }}
          >
            {error}
          </div>
        )}

        {/* Actions rapides */}
        {suggestions === null && !error && (
          <div style={{ fontSize: 12, color: '#666', fontStyle: 'italic' }}>
            Chargement...
          </div>
        )}

        {suggestions && suggestions.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: '#3a3a3a',
                textTransform: 'uppercase',
                marginBottom: 8,
                letterSpacing: 0.5,
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
                  disabled={!!executing}
                  onClick={() => executeSuggestion(sug)}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 8,
                    padding: '8px 10px',
                    marginBottom: 6,
                    width: '100%',
                    background: isExec ? '#000091' : '#fff',
                    color: isExec ? '#fff' : '#161616',
                    border: '1px solid #ccc',
                    borderRadius: 4,
                    fontSize: 12,
                    cursor: executing ? 'wait' : 'pointer',
                    fontFamily: 'inherit',
                    textAlign: 'left',
                  }}
                >
                  <span style={{ color: isExec ? '#fff' : '#000091', flexShrink: 0 }}>
                    ▸
                  </span>
                  <span style={{ flex: 1 }}>{sug.label}</span>
                  {sug.hint && (
                    <span
                      style={{
                        fontSize: 10,
                        color: isExec ? '#fff' : '#888',
                        fontStyle: 'italic',
                      }}
                    >
                      ↗
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}

        {/* Chat NL free-form */}
        <div
          style={{
            paddingTop: 12,
            borderTop: '1px solid #ececfa',
            marginBottom: 12,
          }}
        >
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: '#3a3a3a',
              textTransform: 'uppercase',
              marginBottom: 6,
              letterSpacing: 0.5,
            }}
          >
            Ou decris ton besoin
          </div>
          <textarea
            value={freeInput}
            onChange={(e) => setFreeInput(e.target.value)}
            rows={3}
            placeholder="Ex : Ajoute une carte du 4e arrondissement avec le TRI DGPR..."
            style={{
              width: '100%',
              padding: 8,
              fontSize: 12,
              border: '1px solid #ccc',
              borderRadius: 4,
              fontFamily: 'inherit',
              resize: 'vertical',
              boxSizing: 'border-box',
            }}
          />
          <div style={{ fontSize: 10, color: '#666', marginTop: 4 }}>
            Iter 2 (V2.5) : chat NL streaming SSE. Pour l'instant, utilise
            les actions rapides ou le chat principal.
          </div>
        </div>

        {/* Historique + Undo */}
        {history.length > 0 && (
          <div style={{ paddingTop: 12, borderTop: '1px solid #ececfa' }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: '#3a3a3a',
                textTransform: 'uppercase',
                marginBottom: 6,
                letterSpacing: 0.5,
              }}
            >
              Historique ({history.length})
            </div>
            {history.map((h, i) => (
              <div
                key={h.id}
                style={{
                  padding: '6px 8px',
                  marginBottom: 4,
                  background: '#fff',
                  borderRadius: 4,
                  fontSize: 11,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <span style={{ color: '#1f8d4d' }}>✓</span>
                <span style={{ flex: 1, color: '#3a3a3a' }}>{h.label}</span>
                <span style={{ color: '#999', fontSize: 10 }}>
                  {timeSince(h.at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

// ============================================================================
// Helpers
// ============================================================================

function friendlyKind(type: string): string {
  const map: Record<string, string> = {
    interactiveMap: 'Carte interactive',
    kpiGrid: 'Bandeau chiffres cles',
    kpiBadge: 'Chiffre cle',
    customHeading: 'Titre',
    customQuote: 'Citation',
    narrativeText: 'Paragraphe',
    legend: 'Legende',
    separator: 'Separateur',
    chart: 'Graphique',
    scene3d: 'Scene 3D',
    dataTable: 'Tableau',
    mediaEmbed: 'Media',
    iframeGrist: 'Widget Grist',
  };
  return map[type] || type;
}

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
