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
