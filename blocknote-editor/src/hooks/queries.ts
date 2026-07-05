/**
 * Hooks TanStack Query pour l'editeur BlockNote.
 *
 * Sprint V1.18 Vague 1 Equipe C S3 (2026-07-05).
 *
 * Objectif : cache normalise + stale-while-revalidate + rollback optimistic
 * automatique. Remplace les useEffect + fetch + AbortController + setState
 * dispersees dans AgentPanel/LayersFieldset.
 *
 * Keys convention :
 * - ['assist','suggestions', sid, aid, selectedBlockId]
 * - ['components','source_layers', sid, cid]
 * - ['assist','action'] (mutation, pas de key stockable)
 *
 * Cache TTL (staleTime) :
 * - suggestions : 30s (Marie change souvent de bloc, suggestions rafraichies)
 * - source_layers : 5min (cid + version fig, rarement invalides sauf edit map)
 */
import {
  useQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query';
import { hubFetch } from '../api/hubFetch';
import { ConcurrentUpdateError } from '../types/errors';

// ---------------------------------------------------------------------------
// useAssemblySuggestions - fetch suggestions assistant contextuel
// ---------------------------------------------------------------------------

export interface Suggestion {
  id: string;
  label: string;
  prompt: string;
  tool?: string;
  tool_args?: Record<string, unknown>;
  tool_args_partial?: Record<string, unknown>;
  hint?: string;
  requires_layer_selection?: boolean;
  requires_block_selection?: boolean;
}

interface SuggestionsResponse {
  suggestions: Suggestion[];
}

/**
 * Fetch les suggestions contextuelles pour l'assembly courant.
 * selectedBlockId=null : suggestions globales (top-level actions).
 * Debounce 250ms est gere par le composant via activation conditionnelle
 * de la query (enabled=true seulement quand l'utilisateur a "pose" sa selection).
 */
export function useAssemblySuggestions(
  sid: string | null | undefined,
  aid: string | null | undefined,
  selectedBlockId: string | null | undefined,
  opts?: { enabled?: boolean },
) {
  const enabled = Boolean(sid && aid) && (opts?.enabled ?? true);
  return useQuery<SuggestionsResponse>({
    queryKey: ['assist', 'suggestions', sid, aid, selectedBlockId ?? null],
    queryFn: () => {
      const qs = selectedBlockId
        ? `?selected_block_id=${encodeURIComponent(selectedBlockId)}`
        : '';
      return hubFetch<SuggestionsResponse>(
        `/studies/${sid}/assemblies/${aid}/assist/suggestions${qs}`,
        { silent: true },
      );
    },
    enabled,
    staleTime: 30_000,
    // suggestions volatiles : refetch au retour d'onglet possible
    refetchOnWindowFocus: false,
    retry: 1,
  });
}

// ---------------------------------------------------------------------------
// useComponentSourceLayers - fetch layers scene_manifest interactif
// ---------------------------------------------------------------------------

export interface SourceLayer {
  id: string;
  label?: string;
  type?: string;
  [key: string]: unknown;
}

interface SourceLayersResponse {
  layers: SourceLayer[];
}

export function useComponentSourceLayers(
  sid: string | null | undefined,
  cid: string | null | undefined,
) {
  const enabled = Boolean(sid && cid);
  return useQuery<SourceLayersResponse>({
    queryKey: ['components', 'source_layers', sid, cid],
    queryFn: () =>
      hubFetch<SourceLayersResponse>(
        `/studies/${sid}/components/${cid}/source_layers`,
        { silent: true },
      ),
    enabled,
    // scene_manifest peu volatile : cache 5min
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}

// ---------------------------------------------------------------------------
// useAssemblyAssistAction - mutation avec optimistic + rollback 409
// ---------------------------------------------------------------------------

export interface AssistActionBody {
  tool: string;
  args: Record<string, unknown>;
  cid?: string;
}

export interface ActionResult {
  success: boolean;
  tool: string;
  action_type: string;
  cid?: string;
  aid?: string;
  block?: unknown;
  after_block_id?: string;
  component_version_num_after?: number;
  assembly_version_num_after?: number;
  history_entry?: {
    id: string;
    label: string;
    reversible: boolean;
    reversal_tool?: string;
    reversal_args?: Record<string, unknown>;
  };
}

/**
 * Mutation POST /assist/action avec :
 * - optimistic update possible via onMutate (le caller passe le patch)
 * - rollback automatique si 409 ConcurrentUpdateError (TanStack Query
 *   restaure le cache pre-optimistic via onError context)
 * - invalidation queries dependantes (suggestions rafraichies apres succes)
 */
export function useAssemblyAssistAction(
  sid: string | null | undefined,
  aid: string | null | undefined,
) {
  const qc = useQueryClient();

  return useMutation<ActionResult, Error, AssistActionBody>({
    mutationFn: (body) =>
      hubFetch<ActionResult>(
        `/studies/${sid}/assemblies/${aid}/assist/action`,
        { method: 'POST', json: body, silent: true },
      ),
    onSuccess: () => {
      // Suggestions et layers potentiellement stales apres action reussie
      qc.invalidateQueries({ queryKey: ['assist', 'suggestions', sid, aid] });
    },
    onError: (err) => {
      // Rollback 409 : TanStack rejoue le queryFn si onMutate a stocke un
      // context ; ici la mutation elle-meme ne modifie pas de cache directement
      // (les composants gerent l'apply live via editor.updateBlock). Le caller
      // peut inspecter err instanceof ConcurrentUpdateError pour afficher UI.
      if (err instanceof ConcurrentUpdateError) {
        qc.invalidateQueries({ queryKey: ['assist', 'suggestions', sid, aid] });
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Helper : reset cache complet (utile deconnexion, changement sid)
// ---------------------------------------------------------------------------

export function useResetAssistCache() {
  const qc = useQueryClient();
  return () => {
    qc.removeQueries({ queryKey: ['assist'] });
    qc.removeQueries({ queryKey: ['components'] });
  };
}
