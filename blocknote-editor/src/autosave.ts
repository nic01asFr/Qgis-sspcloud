/**
 * Autosave hook React — Vague E2 Commit H2 (D-QGIS-010).
 *
 * Debounce 30s : à chaque modif user, timer reset. 30s d'inactivité ->
 * appel saveBlocks() qui :
 * 1. Convertit blocks BlockNote en (sections, new_components) via API hub
 * 2. Crée les new_components DOM (POST /components)
 * 3. Remplace les __pending__ refs par les cid réels
 * 4. PUT /assemblies/{aid} avec version_num_source pour concurrency
 *
 * Si conflit (409) : modal "Recharger" ou "Forcer écrasement".
 */
import { useEffect, useRef, useState } from 'react';
import { createComponent, updateAssembly, updateComponent } from './api';
import type { AssemblyManifest } from './types';

const AUTOSAVE_DEBOUNCE_MS = 30_000;

export type SaveStatus =
  | { type: 'idle' }
  | { type: 'pending'; sinceMs: number }
  | { type: 'saving' }
  | { type: 'saved'; atTime: number; versionNum: number }
  | { type: 'error'; message: string }
  | { type: 'conflict'; currentVersionNum: number; sourceVersionNum: number };

/**
 * Convertit blocks BlockNote en sections Assembly via le serializer Python
 * exposé par le hub (POC : pour V1 on fait la conversion côté front via
 * équivalent JS pour éviter un round-trip).
 *
 * V1 simple : reuse local logic similaire à hub/blocknote_serializer.py.
 */
/**
 * Sprint 1 Vague E3 (D3 fix) : sortie enrichie pour distinguer create vs update.
 * - newComponents : DOM blocks SANS cid existant -> POST /components (CREATE)
 * - updatedComponents : DOM blocks AVEC cid existant -> PUT /components/{cid} (UPDATE)
 *   Format : [{cid, manifest}, ...]. saveBlocks fait le PUT versionne.
 */
interface BlocksToSectionsResult {
  sections: any[];
  newComponents: any[];
  updatedComponents: Array<{ cid: string; manifest: any }>;
}

function blocksToSections(blocks: any[]): BlocksToSectionsResult {
  const sections: any[] = [];
  const newComponents: any[] = [];
  const updatedComponents: Array<{ cid: string; manifest: any }> = [];
  let current = { kind: 'section', title: null as string | null, narrative_md: null as string | null, components: [] as any[] };

  const flush = () => {
    if (current.title || current.narrative_md || current.components.length) {
      sections.push({ ...current });
    }
    current = { kind: 'section', title: null, narrative_md: null, components: [] };
  };

  for (const block of blocks) {
    const btype = block.type || '';
    // Heading natif H1/H2 -> nouvelle section
    if (btype === 'heading') {
      const level = block.props?.level || 2;
      if (level <= 2) {
        const title = blockTextContent(block.content);
        // Sprint 3 P2 (8.10) fix D6 : un heading H2 VIDE ne fragmente que si
        // la section courante a deja du contenu. Sinon il est ignore (evite
        // fragmentation involontaire si Marie ajoute 2 H2 vides cote-a-cote
        // ou si le forward serializer pose un marker frontiere sur une
        // section initialement vide).
        const isEmpty = !title;
        const currentHasContent = (
          current.title || current.narrative_md || current.components.length > 0
        );
        if (isEmpty && !currentHasContent) {
          // Skip : ne pas creer de section vide
          continue;
        }
        flush();
        current.title = title;
        continue;
      }
    }
    // Paragraph natif -> narrative_md
    if (btype === 'paragraph') {
      const text = blockTextContent(block.content);
      if (text) {
        current.narrative_md = (current.narrative_md || '') + (current.narrative_md ? '\n\n' : '') + text;
      }
      continue;
    }
    // Custom block -> Component (create OU update selon existingCid)
    const compInfo = customBlockToComponent(block);
    if (!compInfo) continue;
    if (compInfo.refOnly) {
      current.components.push({ ref: compInfo.cid });
    } else if (compInfo.existingCid) {
      // UPDATE : cid existe deja, on met a jour le Component
      updatedComponents.push({
        cid: compInfo.existingCid,
        manifest: compInfo.manifest,
      });
      current.components.push({ ref: compInfo.existingCid });
    } else {
      // CREATE : nouveau Component (utilise placeholder __pending__)
      newComponents.push(compInfo.manifest);
      current.components.push({ ref: '__pending__' });
    }
  }
  flush();
  return { sections, newComponents, updatedComponents };
}

function blockTextContent(content: any): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((c) => (typeof c === 'object' && c?.text ? String(c.text) : typeof c === 'string' ? c : ''))
      .join(' ')
      .trim();
  }
  return '';
}

/**
 * Sprint 1 Vague E3 (D3 fix) : distingue create vs update.
 * - refOnly + cid : iframe block, ref direct vers cid existant
 * - existingCid present : DOM block dont le cid existe deja (UPDATE)
 * - manifest sans existingCid : DOM block nouveau (CREATE)
 */
type CustomBlockMapping =
  | { refOnly: true; cid: string }
  | { refOnly: false; manifest: any; existingCid?: string }
  | null;

function customBlockToComponent(block: any): CustomBlockMapping {
  const TYPE_TO_KIND: Record<string, string> = {
    kpiGrid: 'kpi_grid',
    customHeading: 'heading',
    customQuote: 'quote',
    separator: 'separator',
    kpiBadge: 'kpi_badge',
    legend: 'legend',
    narrativeText: 'narrative_text',
    interactiveMap: 'interactive_map',
    chart: 'chart',
    dataTable: 'data_table',
    scene3d: 'scene_3d',
    mediaEmbed: 'media_embed',
    iframeGrist: 'iframe_grist',
  };
  const kind = TYPE_TO_KIND[block.type || ''];
  if (!kind) return null;
  const props = block.props || {};
  const IFRAME_KINDS = ['interactive_map', 'chart', 'data_table', 'scene_3d', 'media_embed', 'iframe_grist'];
  if (IFRAME_KINDS.includes(kind)) {
    return { refOnly: true, cid: String(props.cid || '') };
  }
  // DOM kind : params via switch (v1.12.4 rollback : tous via props.* vs inline content)
  const params: Record<string, any> = {};
  switch (kind) {
    case 'kpi_grid':
      try { params.kpis = JSON.parse(props.kpisJson || '[]'); } catch { params.kpis = []; }
      params.palette = props.palette || 'monochrome';
      params.columns_min = Number(props.columnsMin || 140);
      break;
    case 'heading':
      params.text = String(props.text || '');
      params.level = Number(props.level || 2);
      break;
    case 'kpi_badge':
      params.value = String(props.value || '');
      params.label = String(props.label || '');
      params.unit = String(props.unit || '');
      params.color = String(props.color || 'info-blue');
      params.source = String(props.source || '');
      break;
    case 'quote':
      params.text = String(props.text || '');
      params.author = String(props.author || '');
      params.source = String(props.source || '');
      break;
    case 'separator':
      params.style = String(props.style || 'solid');
      params.color = String(props.color || '#000091');
      params.variant = String(props.variant || 'rule');
      break;
    case 'legend':
      try { params.items = JSON.parse(props.itemsJson || '[]'); } catch { params.items = []; }
      params.title = String(props.title || '');
      params.source = String(props.source || '');
      break;
    case 'narrative_text':
      params.content = String(props.content || '');
      break;
  }
  // Sprint 1 Vague E3 (D3 fix) : si props.cid existe -> UPDATE existant
  // sinon CREATE nouveau.
  const existingCid = String(props.cid || '');
  return {
    refOnly: false,
    manifest: {
      kind,
      title: params.text || params.label || params.title || kind,
      classification: 'cerema_internal',
      params,
      rendering: { runtime: 'html', container_size: 'full' },
    },
    ...(existingCid ? { existingCid } : {}),
  };
}

/**
 * Effectue la sauvegarde complète d'un document BlockNote vers un Assembly.
 *
 * Workflow :
 * 1. blocksToSections() : convert BlockNote -> sections + new_components
 * 2. Pour chaque new_component (DOM) : POST /components -> cid réel
 * 3. Replace __pending__ refs par les cid
 * 4. PUT /assemblies/{aid} avec version_num_source pour concurrency
 *
 * @returns success : { ok: true, versionNum }
 * @returns conflict : { ok: false, conflict: {currentVersionNum, sourceVersionNum} }
 * @returns error : { ok: false, error: 'message' }
 */
export async function saveBlocks(
  sid: string,
  aid: string,
  blocks: any[],
  existingManifest: AssemblyManifest,
  // Audit v1.7.2 P1 #2 : versionNumSource = null bypass concurrency check
  // côté hub (utilisé pour 'Forcer écrasement' après conflit 409).
  versionNumSource: number | null,
  // Audit fix v1.7.1 P0 #4 : reusableCids permet de récupérer des cid déjà
  // créés lors d'un essai précédent qui a échoué au PUT (évite composants
  // orphelins). Le caller (useAutosave) maintient cette ref entre essais.
  reusableCids: string[] = [],
): Promise<{ ok: boolean; newVersionNum?: number; error?: string; conflict?: any; createdCids?: string[] }> {
  const { sections, newComponents, updatedComponents } = blocksToSections(blocks);

  // Sprint 1 Vague E3 (D3 fix) : UPDATE les Components DOM existants en
  // parallele (PUT versionne au lieu de POST nouveaux = pollution).
  // versionNumSource=null bypass OCC : on assume que Marie a chargee l'editeur
  // avec la version actuelle des components (sinon elle aurait recharge avant).
  if (updatedComponents.length > 0) {
    try {
      await Promise.all(
        updatedComponents.map(({ cid, manifest }) =>
          updateComponent(sid, cid, manifest, null).then((r) => {
            if (!r.ok) throw new Error(r.error || 'update component echec');
            return r;
          }),
        ),
      );
    } catch (err) {
      return {
        ok: false,
        error: `Échec update composants : ${String(err)}`,
      };
    }
  }

  // Créer les new_components DOM côté hub.
  // Audit fix v1.7.1 P0 #4 : parallèle Promise.all + réutilisation cid retry.
  const createdCids: string[] = [...reusableCids];
  const toCreate = newComponents.slice(reusableCids.length);
  if (toCreate.length > 0) {
    try {
      const results = await Promise.all(
        toCreate.map((manifest) => createComponent(sid, manifest)),
      );
      for (const r of results) createdCids.push(r.id);
    } catch (err) {
      return {
        ok: false,
        error: `Échec création composants : ${String(err)}`,
        createdCids, // retourne ce qu'on a réussi, le caller pourra retry
      };
    }
  }

  // Remplacer __pending__ refs par cid réels
  let pendingIdx = 0;
  for (const section of sections) {
    for (let i = 0; i < section.components.length; i++) {
      if (section.components[i].ref === '__pending__') {
        if (pendingIdx >= createdCids.length) {
          return {
            ok: false,
            error: 'Désynchronisation __pending__ refs vs created cids',
            createdCids,
          };
        }
        section.components[i].ref = createdCids[pendingIdx++];
      }
    }
  }

  // PUT update_assembly
  const newManifest = {
    ...existingManifest,
    layout: { ...(existingManifest.layout || { type: 'scroll_vertical' }), sections },
  };
  const result = await updateAssembly(sid, aid, newManifest, versionNumSource);
  // En cas d'échec du PUT, on retourne les cid créés pour réutilisation
  return { ...result, createdCids };
}

/**
 * Hook React useAutosave : debounce 30s à chaque modif blocks.
 */
export function useAutosave(
  enabled: boolean,
  sid: string,
  aid: string,
  blocks: any[] | null,
  existingManifest: AssemblyManifest | null,
  versionNumSource: number,
  onSaveSuccess: (newVersionNum: number) => void,
): SaveStatus {
  const [status, setStatus] = useState<SaveStatus>({ type: 'idle' });
  const timerRef = useRef<number | null>(null);
  // Audit fix v1.7.1 P0 #1 : lastBlocksRef ne reflète que l'état SAUVEGARDÉ.
  // Sans ça, si un save échoue (409, 500), le `lastBlocksRef` reflète l'état
  // tenté → un keystroke identique à l'état échoué est ignoré tant qu'aucun
  // autre changement ne survient.
  const lastSavedBlocksRef = useRef<string | null>(null);
  // Audit fix v1.7.1 P0 #4 : composants déjà créés sur un essai antérieur
  // qui a échoué au PUT (concurrency, validation). Réutilisés au prochain
  // essai pour éviter les composants orphelins côté hub.
  const reusableCidsRef = useRef<string[]>([]);

  useEffect(() => {
    if (!enabled || !blocks || !existingManifest) return;
    const blocksJson = JSON.stringify(blocks);
    if (lastSavedBlocksRef.current === null) {
      // Premier render : enregistrer le baseline, pas de save
      lastSavedBlocksRef.current = blocksJson;
      return;
    }
    if (lastSavedBlocksRef.current === blocksJson) {
      // Pas de changement vs dernier état sauvegardé
      return;
    }
    setStatus({ type: 'pending', sinceMs: Date.now() });

    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
    }
    timerRef.current = window.setTimeout(async () => {
      setStatus({ type: 'saving' });
      try {
        const result = await saveBlocks(
          sid, aid, blocks, existingManifest, versionNumSource,
          reusableCidsRef.current,
        );
        if (result.ok && result.newVersionNum) {
          // Audit fix v1.7.1 P0 #1 : update lastSavedBlocksRef SEULEMENT
          // après succès. Reset les cid réutilisables (consommés).
          lastSavedBlocksRef.current = blocksJson;
          reusableCidsRef.current = [];
          setStatus({ type: 'saved', atTime: Date.now(), versionNum: result.newVersionNum });
          onSaveSuccess(result.newVersionNum);
        } else {
          // Audit fix v1.7.1 P0 #4 : conserver les cid créés pour retry
          if (result.createdCids) {
            reusableCidsRef.current = result.createdCids;
          }
          if (result.conflict) {
            setStatus({ type: 'conflict', ...result.conflict });
          } else {
            setStatus({ type: 'error', message: result.error || 'Erreur inconnue' });
          }
        }
      } catch (err) {
        setStatus({ type: 'error', message: String(err) });
      }
    }, AUTOSAVE_DEBOUNCE_MS);

    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, [enabled, blocks, sid, aid, existingManifest, versionNumSource, onSaveSuccess]);

  return status;
}
