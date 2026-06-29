/**
 * Client API hub qgis-sspcloud pour l'éditeur BlockNote.
 *
 * Vague E2 Commit E2 (D-QGIS-010) : fetch read-only.
 * Auth via cookie OIDC same-origin (la page /editor/... est servie par
 * le hub authentifié, donc cookie propagé en iframe/embed).
 */
import type { AssemblyFetchResponse, ComponentManifest } from './types';

const API_BASE = ''; // same-origin

export async function fetchAssembly(
  sid: string,
  aid: string,
): Promise<AssemblyFetchResponse> {
  const url = `${API_BASE}/studies/${sid}/assemblies/${aid}`;
  const res = await fetch(url, { credentials: 'include' });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Fetch assembly ${aid} : HTTP ${res.status} — ${detail.slice(0, 200)}`);
  }
  return await res.json();
}

export async function fetchComponent(
  sid: string,
  cid: string,
): Promise<ComponentManifest> {
  const url = `${API_BASE}/studies/${sid}/components/${cid}`;
  const res = await fetch(url, { credentials: 'include' });
  if (!res.ok) {
    throw new Error(`Fetch component ${cid} : HTTP ${res.status}`);
  }
  const data = await res.json();
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
  const url = `${API_BASE}/studies/${sid}/components`;
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(manifest),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Create component : HTTP ${res.status} — ${detail.slice(0, 200)}`);
  }
  return await res.json();
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
  const url = `${API_BASE}/studies/${sid}/components/${cid}`;
  const res = await fetch(url, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (res.status === 409) {
    const data = await res.json().catch(() => ({}));
    const detail = data.detail || data;
    return {
      ok: false,
      conflict: {
        currentVersionNum: detail.current_version_num || 0,
        sourceVersionNum: detail.source_version_num || 0,
      },
      error: detail.message || 'Conflit version composant',
    };
  }
  if (!res.ok) {
    const text = await res.text();
    return { ok: false, error: `HTTP ${res.status} — ${text.slice(0, 200)}` };
  }
  const data = await res.json();
  return { ok: true, newVersionNum: data.version_num, cid: data.id };
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
  const url = `${API_BASE}/studies/${sid}/assemblies/${aid}`;
  const res = await fetch(url, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (res.status === 409) {
    const data = await res.json().catch(() => ({}));
    const detail = data.detail || data;
    return {
      ok: false,
      conflict: {
        currentVersionNum: detail.current_version_num || 0,
        sourceVersionNum: detail.source_version_num || 0,
      },
      error: detail.message || 'Conflit version',
    };
  }
  if (!res.ok) {
    const text = await res.text();
    return { ok: false, error: `HTTP ${res.status} — ${text.slice(0, 200)}` };
  }
  const data = await res.json();
  return { ok: true, newVersionNum: data.version_num };
}
