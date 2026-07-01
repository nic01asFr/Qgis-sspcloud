/**
 * AssistantCard — Assistant IA contextuel scoped composant carte.
 *
 * Sprint 2.5 V2.5 A2d POC (etude B architecture cible 2026-06-30).
 *
 * Pattern Notion AI : chips actions contextuelles + textarea NL.
 * Marie voit 5 suggestions au mount (POC hardcoded par kind, iter 2 =
 * dynamiques via LLM cached). Clique une chip = execute directement le
 * tool cmp_* correspondant (pas de LLM). Cas complexe = textarea NL
 * (iter 2 = appel agent scope component-only + streaming SSE).
 *
 * Livre le backend attendu par le F8 quick win V1.13.5 :
 * bouton "+ Ajouter une couche" dans LayersFieldset qui appelle
 * window.__openAssistantWithPrompt.
 *
 * Endpoints hub consomes :
 * - GET  /studies/{sid}/components/{cid}/assist/suggestions
 * - POST /studies/{sid}/components/{cid}/assist/action  {tool, args}
 */
import { useEffect, useState } from 'react';

type Suggestion = {
  id: string;
  label: string;
  prompt: string;
  hint?: string;
  tool?: string;
  tool_args?: Record<string, any>;
  tool_args_partial?: Record<string, any>;
  requires_layer_selection?: boolean;
};

type ActionResult = {
  success: boolean;
  tool: string;
  version_num_after?: number;
};

export function AssistantCard({
  sid,
  cid,
  onActionApplied,
}: {
  sid: string | undefined;
  cid: string | undefined;
  onActionApplied?: (result: ActionResult) => void;
}) {
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [executing, setExecuting] = useState<string | null>(null);
  const [freeInput, setFreeInput] = useState('');
  const [lastResult, setLastResult] = useState<string | null>(null);

  useEffect(() => {
    if (!sid || !cid) return;
    const ctrl = new AbortController();
    fetch(`/studies/${sid}/components/${cid}/assist/suggestions`, {
      credentials: 'include',
      signal: ctrl.signal,
    })
      .then((r) => {
        if (!r.ok) throw new Error(`assist/suggestions ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (ctrl.signal.aborted) return;
        setSuggestions(data.suggestions || []);
      })
      .catch((e) => {
        if (ctrl.signal.aborted || e?.name === 'AbortError') return;
        setError(String(e?.message || e));
      });
    return () => ctrl.abort();
  }, [sid, cid]);

  const executeSuggestion = async (sug: Suggestion) => {
    if (!sid || !cid) return;

    // Cas "hint" seul : pas de tool, escalade vers agent complet.
    if (sug.hint && !sug.tool) {
      setLastResult(
        "Cette action necessite l'agent IA complet. " +
        "Ouvre le chat CEREMA (icone bulle en bas a droite) et copie-colle : " +
        `"${sug.prompt}"`
      );
      return;
    }

    // Cas requires_layer_selection : pas encore d'UI de selection layer
    // dans le POC. Iter 2 = ouvrir modal selection layer.
    if (sug.requires_layer_selection && !sug.tool_args?.layer_id_ref) {
      setLastResult(
        "Cette action necessite de choisir une couche. " +
        "Utilise la section Couches ci-dessous pour selectionner la couche cible, " +
        "puis reviens cliquer cette suggestion."
      );
      return;
    }

    setExecuting(sug.id);
    setError(null);
    try {
      const resp = await fetch(
        `/studies/${sid}/components/${cid}/assist/action`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            tool: sug.tool,
            args: sug.tool_args || {},
          }),
        },
      );
      if (resp.status === 409) {
        setError(
          "Conflit : cette carte a ete modifiee entre-temps. Recharge la page."
        );
        return;
      }
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as ActionResult;
      setLastResult(`Action appliquee : ${sug.label}. La carte se rafraichit...`);
      if (onActionApplied) onActionApplied(data);
      // Recharger la page apres 1s pour voir le rendu carte mis a jour
      setTimeout(() => window.location.reload(), 1500);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setExecuting(null);
    }
  };

  // Global bridge : le bouton "+ Ajouter une couche" F8 V1.13.5 appelle
  // window.__openAssistantWithPrompt. On l'expose ici.
  useEffect(() => {
    (window as any).__openAssistantWithPrompt = (prompt: string) => {
      setFreeInput(prompt);
      // Scroll vers l'assistant card
      const el = document.querySelector('[data-assistant-card]');
      if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
    };
    return () => {
      delete (window as any).__openAssistantWithPrompt;
    };
  }, []);

  if (!sid || !cid) return null;

  return (
    <div
      data-assistant-card
      style={{
        border: '2px solid #000091',
        background: 'linear-gradient(135deg, #f5f5fe 0%, #ffffff 100%)',
        borderRadius: 6,
        padding: 16,
        marginBottom: 24,
      }}
    >
      <div
        style={{
          fontSize: 13,
          fontWeight: 700,
          color: '#000091',
          marginBottom: 4,
          textTransform: 'uppercase',
          letterSpacing: 0.5,
        }}
      >
        Assistant CEREMA
      </div>
      <div style={{ fontSize: 12, color: '#666', marginBottom: 12 }}>
        Que veux-tu faire avec cette carte ?
      </div>

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

      {lastResult && (
        <div
          style={{
            padding: 10,
            background: '#e8f5e9',
            border: '1px solid #a5d6a7',
            borderRadius: 4,
            fontSize: 12,
            color: '#2e7d32',
            marginBottom: 12,
          }}
        >
          {lastResult}
        </div>
      )}

      {suggestions === null && !error && (
        <div style={{ fontSize: 12, color: '#666', fontStyle: 'italic' }}>
          Chargement des suggestions...
        </div>
      )}

      {suggestions && suggestions.length > 0 && (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
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
                    alignItems: 'center',
                    gap: 8,
                    padding: '8px 12px',
                    background: isExec ? '#000091' : '#fff',
                    color: isExec ? '#fff' : '#161616',
                    border: '1px solid #ccc',
                    borderRadius: 4,
                    fontSize: 12,
                    cursor: executing ? 'wait' : 'pointer',
                    fontFamily: 'inherit',
                    textAlign: 'left',
                    fontWeight: 500,
                  }}
                >
                  <span style={{ color: '#000091', fontWeight: 700 }}>▸</span>
                  <span style={{ flex: 1 }}>{sug.label}</span>
                  {isExec && (
                    <span style={{ fontSize: 10, opacity: 0.7 }}>
                      Application...
                    </span>
                  )}
                  {sug.hint && (
                    <span
                      style={{
                        fontSize: 10,
                        color: isExec ? '#fff' : '#888',
                        fontStyle: 'italic',
                      }}
                      title={sug.hint}
                    >
                      (agent complet)
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div
            style={{
              marginTop: 12,
              paddingTop: 12,
              borderTop: '1px solid #e0e0e0',
              fontSize: 11,
              color: '#666',
            }}
          >
            Ou decris ton besoin en francais :
          </div>
          <textarea
            value={freeInput}
            onChange={(e) => setFreeInput(e.target.value)}
            rows={3}
            placeholder="Ex : Colore les batiments selon leur hauteur, avec une palette rouge-jaune-vert."
            style={{
              width: '100%',
              marginTop: 6,
              padding: 8,
              fontSize: 12,
              border: '1px solid #ccc',
              borderRadius: 4,
              fontFamily: 'inherit',
              resize: 'vertical',
              boxSizing: 'border-box',
            }}
          />
          <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>
            Iter 2 : ouvrira le chat agent CEREMA scoped au composant.
            Pour l'instant, utilise le chat principal (icone bulle en bas a droite).
          </div>
        </>
      )}

      {suggestions && suggestions.length === 0 && (
        <div style={{ fontSize: 12, color: '#666', fontStyle: 'italic' }}>
          Aucune suggestion disponible pour ce type de composant.
        </div>
      )}
    </div>
  );
}
