/**
 * Client API hub qgis-sspcloud pour l'éditeur BlockNote.
 *
 * Vague E2 Commit E2 (D-QGIS-010) : fetch read-only.
 * Auth via cookie OIDC same-origin (la page /editor/... est servie par
 * le hub authentifié, donc cookie propagé en iframe/embed).
 *
 * Sprint V1.18 Vague 1 Equipe C R3 (2026-07-05) : migre vers hubFetch qui
 * hydrate les erreurs typees (ApiError sous-classes) au lieu de throw Error
 * generique. Les callers qui veulent afficher un statut inline (conflict UI
 * dans autosave) attrapent ConcurrentUpdateError explicitement.
 */
import type { AssemblyFetchResponse, ComponentManifest } from './types';
import { hubFetch } from './api/hubFetch';
import { ApiError, ConcurrentUpdateError } from './types/errors';

const API_BASE = ''; // same-origin

export async function fetchAssembly(
  sid: string,
  aid: string,
): Promise<AssemblyFetchResponse> {
  return hubFetch<AssemblyFetchResponse>(
    `${API_BASE}/studies/${sid}/assemblies/${aid}`,
  );
}

export async function fetchComponent(
  sid: string,
  cid: string,
): Promise<ComponentManifest> {
  const data = await hubFetch<any>(`${API_BASE}/studies/${sid}/components/${cid}`);
  // Retourne le manifest (la response wrapper a metadata + manifest)
  return data.manifest || data;
}

/**
 * Crée un nouveau Component (DOM kind) avant update_assembly.
 * Utilisé par H1 autosave pour les blocks DOM qui n'ont pas encore de cid.
 */
export async function createComponent(
  sid: string,
  manifest: any,
): Promise<{ id: string }> {
  return hubFetch<{ id: string }>(`${API_BASE}/studies/${sid}/components`, {
    method: 'POST',
    json: manifest,
  });
}

/**
 * Sprint 1 Vague E3 (D3 fix) : update versionne d'un Component existant.
 *
 * Permet a BlockNote de PUT au lieu de POST quand Marie modifie un block
 * DOM dont le cid existe deja. Sans ca, on creait un nouveau Component
 * a chaque save -> pollution PVC/DB/audit_chain.
 *
 * @param sid - 12 hex etude id
 * @param cid - 12 hex component id existant
 * @param manifest - Component manifest top-level (params, title, etc.)
 * @param versionNumSource - version_num au load pour OCC (null = bypass)
 *
 * Retourne {ok: true, newVersionNum, cid} en cas de succes,
 * sinon {ok: false, conflict?, error?}.
 */
export interface UpdateComponentResult {
  ok: boolean;
  newVersionNum?: number;
  cid?: string;
  conflict?: {
    currentVersionNum: number;
    sourceVersionNum: number;
  };
  error?: string;
}

export async function updateComponent(
  sid: string,
  cid: string,
  manifest: any,
  versionNumSource: number | null,
): Promise<UpdateComponentResult> {
  const body = { ...manifest };
  if (versionNumSource !== null) {
    body.version_num_source = versionNumSource;
  }
  try {
    const data = await hubFetch<any>(
      `${API_BASE}/studies/${sid}/components/${cid}`,
      { method: 'PUT', json: body, silent: true },
    );
    return { ok: true, newVersionNum: data.version_num, cid: data.id };
  } catch (err) {
    if (err instanceof ConcurrentUpdateError) {
      return {
        ok: false,
        conflict: {
          currentVersionNum: err.current,
          sourceVersionNum: err.source,
        },
        error: err.message || 'Conflit version composant',
      };
    }
    if (err instanceof ApiError) {
      return { ok: false, error: `HTTP ${err.statusCode} — ${err.message}` };
    }
    return { ok: false, error: String(err) };
  }
}

/**
 * Update Assembly avec optimistic concurrency control.
 *
 * Vague E2 Commit H1 (D-QGIS-010) : si version_num_source fourni,
 * le hub renvoie HTTP 409 en cas de conflit (autre processus a modifié
 * l'assembly entre temps).
 */
export interface UpdateAssemblyResult {
  ok: boolean;
  newVersionNum?: number;
  conflict?: {
    currentVersionNum: number;
    sourceVersionNum: number;
  };
  error?: string;
}

export async function updateAssembly(
  sid: string,
  aid: string,
  manifest: any,
  versionNumSource: number | null,
): Promise<UpdateAssemblyResult> {
  const body = { ...manifest };
  if (versionNumSource !== null) {
    body.version_num_source = versionNumSource;
  }
  try {
    const data = await hubFetch<any>(
      `${API_BASE}/studies/${sid}/assemblies/${aid}`,
      { method: 'PUT', json: body, silent: true },
    );
    return { ok: true, newVersionNum: data.version_num };
  } catch (err) {
    if (err instanceof ConcurrentUpdateError) {
      return {
        ok: false,
        conflict: {
          currentVersionNum: err.current,
          sourceVersionNum: err.source,
        },
        error: err.message || 'Conflit version',
      };
    }
    if (err instanceof ApiError) {
      return { ok: false, error: `HTTP ${err.statusCode} — ${err.message}` };
    }
    return { ok: false, error: String(err) };
  }
}
