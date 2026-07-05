/**
 * Types auto-generated from hub FastAPI /openapi.json
 *
 * Sprint V2.0 Phase 1 Etape 2 - S1 openapi-typescript (2026-07-05).
 *
 * Regenerate : `pnpm gen:types` (necessite hub running localhost:8000)
 * ou en CI : job auto qui hit /openapi.json du deployment prod.
 *
 * NE PAS EDITER MANUELLEMENT ce fichier - il sera ecrase a chaque regen.
 *
 * V1 : placeholder minimal - types manuels de src/types.ts sont progressivement
 * migres vers ce fichier. La CI GitHub Actions completera la generation reelle
 * en Sprint 1.1 Etape 4 (S2) avec un job qui verifie que /openapi.json n'a
 * pas evolue sans PR.
 */

/** Common Pydantic model shapes from hub/hub/actions/types.py */
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

export interface HistoryEntry {
  id: string;
  actor: string;
  timestamp: string;
  tool: string;
  args_json?: string;
  action_type: string;
  label: string;
  reversible: boolean;
  reversal_tool?: string;
  reversal_args_json?: string;
  version_before?: number;
  version_after?: number;
}

export interface ActionResult {
  success: boolean;
  tool: string;
  action_type: string;
  cid?: string;
  aid?: string;
  block?: BlockRef;
  after_block_id?: string;
  component_version_num_after?: number;
  assembly_version_num_after?: number;
  history_entry?: HistoryEntry;
  warning?: string;
  telemetry?: Record<string, unknown>;
}

export interface BlockRef {
  block_id: string;
  kind: string;
  params?: Record<string, unknown>;
  component_ref?: { cid: string; version_num_pinned?: number };
  section_id?: string;
}

/** Assembly manifest from hub/hub/models/assembly.py */
export interface AssemblyManifest {
  title: string;
  audience?: string;
  layout?: {
    type?: string;
    sections?: Array<{
      kind?: string;
      title?: string | null;
      narrative_md?: string | null;
      components?: Array<{ ref?: string; inline?: Record<string, unknown> }>;
    }>;
  };
  classification?: string;
  provenance?: Record<string, unknown>;
}

export interface AssemblyFetchResponse {
  metadata?: {
    aid?: string;
    version_num?: number;
    owner?: string;
    created_at?: string;
    updated_at?: string;
  };
  manifest: AssemblyManifest;
}

/** Component manifest from hub/hub/models/component.py */
export interface ComponentManifest {
  kind: string;
  title?: string;
  classification?: string;
  params: Record<string, unknown>;
  rendering?: { runtime?: string; container_size?: string };
}

/** Typed HTTP errors from hub/hub/actions/errors.py */
export type HubErrorKind =
  | "scope_violation"
  | "concurrent_update"
  | "action_validation"
  | "action_not_found"
  | "tool_not_allowed"
  | "persistence_error"
  | "network_error"
  | "unknown";

export interface HubErrorBody {
  kind: HubErrorKind;
  message: string;
  status_code: number;
  details?: Record<string, unknown>;
}
