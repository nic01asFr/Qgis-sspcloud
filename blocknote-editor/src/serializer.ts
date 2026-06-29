/**
 * Sérialisation Assembly Pydantic <-> BlockNote document.
 *
 * Vague E2 Commit F1-F3 (D-QGIS-010) : V1 forward only (Assembly -> BlockNote).
 * Round-trip complet (BlockNote -> Assembly) sera livré Commit G.
 */
import type { AssemblyManifest, ComponentManifest } from './types';
import { componentKindToBlockType } from './blocks';
import { fetchComponent } from './api';

/**
 * Convertit un Assembly + ses composants en blocks BlockNote.
 *
 * Pour chaque AssemblySection :
 * 1. Si section.title -> heading natif H2
 * 2. Si section.narrative_md -> paragraph (V1 simple)
 * 3. Pour chaque component ref -> fetch component + mapping kind -> block
 */
export async function assemblyToBlockNoteDoc(
  sid: string,
  assembly: AssemblyManifest,
): Promise<any[]> {
  const blocks: any[] = [];

  for (const section of assembly.layout?.sections || []) {
    // Section title -> heading natif H2
    if (section.title) {
      blocks.push({
        type: 'heading',
        props: { level: 2 },
        content: section.title,
      });
    }

    // Narrative_md -> paragraph
    if (section.narrative_md) {
      blocks.push({
        type: 'paragraph',
        content: section.narrative_md.slice(0, 1000),
      });
    }

    // Components -> custom blocks via mapping kind -> block type
    for (const compRef of section.components || []) {
      if (!compRef.ref) continue;
      try {
        const component = await fetchComponent(sid, compRef.ref);
        const blockType = componentKindToBlockType(component.kind);
        if (!blockType) {
          // Kind pas encore supporté (F4+F5 à venir) — placeholder
          blocks.push({
            type: 'paragraph',
            content: [
              { type: 'text', text: '📎 Composant ', styles: { bold: true } },
              { type: 'text', text: component.kind, styles: { code: true } },
              {
                type: 'text',
                text: ` (${compRef.ref.slice(0, 8)}…) — block iframe à venir F4+F5`,
                styles: { italic: true, textColor: 'gray' },
              },
            ],
          });
          continue;
        }
        // Custom block DOM : convertir params -> props
        const params = (component.params || {}) as Record<string, any>;
        const props: Record<string, any> = { cid: compRef.ref };

        switch (component.kind) {
          case 'kpi_grid':
            props.kpisJson = JSON.stringify(params.kpis || []);
            props.palette = params.palette || 'monochrome';
            props.columnsMin = params.columns_min || 140;
            break;
          case 'heading':
            props.level = params.level || 2;
            props.text = params.text || component.title || '';
            break;
          case 'kpi_badge':
            props.value = String(params.value || '');
            props.label = params.label || '';
            props.unit = params.unit || '';
            props.color = params.color || 'info-blue';
            props.source = params.source || '';
            break;
          case 'quote':
            props.text = params.text || '';
            props.author = params.author || '';
            props.source = params.source || '';
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
            props.content = params.content || params.markdown || params.text || '';
            break;
          default:
            break;
        }

        blocks.push({
          type: blockType,
          props,
        });
      } catch (err) {
        // Component fetch failed -> placeholder erreur
        blocks.push({
          type: 'paragraph',
          content: [
            {
              type: 'text',
              text: `⚠️ Erreur composant ${compRef.ref.slice(0, 8)}… : ${String(err).slice(0, 80)}`,
              styles: { textColor: 'red' },
            },
          ],
        });
      }
    }
  }

  // Fallback : assembly vide
  if (blocks.length === 0) {
    blocks.push({
      type: 'paragraph',
      content: 'Assembly vide.',
    });
  }

  return blocks;
}
