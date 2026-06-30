/**
 * Sérialisation Assembly Pydantic <-> BlockNote document.
 *
 * Vague E2 Commit F1-F5 (D-QGIS-010) + audit v1.7.2 optims P1 :
 * - V1 forward only (Assembly -> BlockNote)
 * - Round-trip BlockNote -> Assembly via blocksToSections (autosave.ts)
 * - Parallelisation Promise.all fetchComponent (gain ouverture 4s -> 1s)
 */
import type { AssemblyManifest } from './types';
import { componentKindToBlockType } from './blocks';
import { fetchComponent } from './api';

/**
 * Convertit un Assembly + ses composants en blocks BlockNote.
 *
 * Audit v1.7.2 P1 #1 : tous les fetchComponent sont lancés EN PARALLÈLE
 * via Promise.all (vs séquentiel V1.7.1 = ~200ms/composant × N).
 *
 * Pour chaque AssemblySection :
 * 1. Si section.title -> heading natif H2
 * 2. Si section.narrative_md -> paragraph
 * 3. Pour chaque component ref -> fetch component (parallèle) + mapping
 */
export async function assemblyToBlockNoteDoc(
  sid: string,
  assembly: AssemblyManifest,
): Promise<any[]> {
  const sections = assembly.layout?.sections || [];

  // Audit v1.7.2 P1 #1 : pré-récupérer TOUS les composants en parallèle.
  // Map<cid, ComponentManifest | Error> pour ne pas re-fetcher + survivre
  // aux 404/timeouts individuels (Promise.allSettled).
  const allCids = new Set<string>();
  for (const section of sections) {
    for (const compRef of section.components || []) {
      if (compRef.ref) allCids.add(compRef.ref);
    }
  }

  const cidList = Array.from(allCids);
  const fetchResults = await Promise.allSettled(
    cidList.map((cid) => fetchComponent(sid, cid)),
  );
  const componentByCid = new Map<string, any | Error>();
  for (let i = 0; i < cidList.length; i++) {
    const res = fetchResults[i];
    if (res.status === 'fulfilled') {
      componentByCid.set(cidList[i], res.value);
    } else {
      componentByCid.set(cidList[i], new Error(String(res.reason)));
    }
  }

  // Construction des blocks dans l'ordre déclaratif
  const blocks: any[] = [];

  // v1.7.4 fix : TOUJOURS insérer un heading natif H2 par section pour
  // marquer la frontière. Sinon le backend round-trip blocksToSections()
  // fusionne toutes les sections en une seule (aucun marqueur entre elles).
  // Si section.title vide, on utilise un placeholder discret pour préserver
  // la structure (sera retiré au save si user vide).
  for (let i = 0; i < sections.length; i++) {
    const section = sections[i];
    const isFirstSection = i === 0;
    const hasMarker =
      section.title ||
      // 1ère section : pas besoin de marqueur (current = nouvelle section au reset)
      !isFirstSection;
    if (hasMarker) {
      blocks.push({
        type: 'heading',
        props: { level: 2 },
        content: section.title || '',
      });
    }

    if (section.narrative_md) {
      blocks.push({
        type: 'paragraph',
        content: section.narrative_md,
      });
    }

    for (const compRef of section.components || []) {
      if (!compRef.ref) continue;
      const component = componentByCid.get(compRef.ref);

      // Échec fetch -> placeholder erreur
      if (component instanceof Error) {
        blocks.push({
          type: 'paragraph',
          content: [
            {
              type: 'text',
              text: `⚠️ Erreur composant ${compRef.ref.slice(0, 8)}… : ${component.message.slice(0, 80)}`,
              styles: { textColor: 'red' },
            },
          ],
        });
        continue;
      }

      const blockType = componentKindToBlockType(component.kind);
      if (!blockType) {
        blocks.push({
          type: 'paragraph',
          content: [
            { type: 'text', text: '📎 Composant ', styles: { bold: true } },
            { type: 'text', text: component.kind, styles: { code: true } },
            {
              type: 'text',
              text: ` (${compRef.ref.slice(0, 8)}…) — kind non encore mappé`,
              styles: { italic: true, textColor: 'gray' },
            },
          ],
        });
        continue;
      }

      const params = (component.params || {}) as Record<string, any>;
      const props: Record<string, any> = { cid: compRef.ref };
      // v1.11 Phase A : content inline pour les 3 blocks texte (vs props.text)
      let inlineContent: any = null;

      switch (component.kind) {
        // ── DOM blocks ──
        case 'kpi_grid':
          props.kpisJson = JSON.stringify(params.kpis || []);
          props.palette = params.palette || 'monochrome';
          props.columnsMin = params.columns_min || 140;
          break;
        case 'heading':
          // v1.11 : level reste en prop, text passe en content inline
          props.level = params.level || 2;
          inlineContent = params.text || component.title || '';
          break;
        case 'kpi_badge':
          props.value = String(params.value || '');
          props.label = params.label || '';
          props.unit = params.unit || '';
          props.color = params.color || 'info-blue';
          props.source = params.source || '';
          break;
        case 'quote':
          // v1.11 : text passe en content inline, author/source restent en props
          props.author = params.author || '';
          props.source = params.source || '';
          inlineContent = params.text || '';
          break;
        case 'separator':
          props.style = params.style || 'solid';
          props.color = params.color || '#000091';
          props.variant = params.variant || 'rule';
          break;
        case 'legend':
          props.itemsJson = JSON.stringify(params.items || []);
          props.title = params.title || component.title || '';
          props.source = params.source || '';
          break;
        case 'narrative_text':
          // v1.11 : content passe en inline editable
          inlineContent = params.content || params.markdown || params.text || '';
          break;
        // ── Iframe blocks ──
        case 'interactive_map':
          // v1.12 : exposer les params editables dans props (form Edit Panel
          // lit depuis block.props pour init le form)
          props.sid = sid;
          props.title = params.title || component.title || '';
          props.subtitle = params.subtitle || '';
          props.description = params.description || '';
          props.basemap_id = params.basemap_id || 'osm';
          props.source = params.source || '';
          props.caveat = params.caveat || '';
          props.height = params.height || 580;
          break;
        case 'chart':
        case 'data_table':
        case 'scene_3d':
        case 'media_embed':
        case 'iframe_grist':
          props.sid = sid;
          break;
        default:
          break;
      }

      const blockData: any = { type: blockType, props };
      if (inlineContent !== null) {
        blockData.content = inlineContent;
      }
      blocks.push(blockData);
    }
  }

  if (blocks.length === 0) {
    blocks.push({
      type: 'paragraph',
      content: 'Assembly vide.',
    });
  }

  return blocks;
}
