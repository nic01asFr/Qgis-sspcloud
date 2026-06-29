/**
 * Index des custom blocks Vague E2 (D-QGIS-010).
 *
 * Export central + schema BlockNote pour useCreateBlockNote.
 *
 * V1 livré (F1+F2+F3) : 6 custom blocks DOM
 *   kpiGrid, customHeading, customQuote, separator, kpiBadge, legend, narrativeText
 *
 * F4+F5 à venir : 6 iframe blocks (interactive_map, chart, data_table,
 *                                    scene_3d, media_embed, iframe_grist)
 */
import {
  BlockNoteSchema,
  defaultBlockSpecs,
} from '@blocknote/core';
import { KpiGridBlock } from './KpiGrid';
import { CustomHeadingBlock } from './CustomHeading';
import { CustomQuoteBlock } from './CustomQuote';
import { SeparatorBlock } from './Separator';
import { KpiBadgeBlock } from './KpiBadge';
import { LegendBlock } from './Legend';
import { NarrativeTextBlock } from './NarrativeText';

/**
 * Schema BlockNote avec custom blocks Vague E2.
 *
 * Use case : passé à useCreateBlockNote({schema}) pour permettre à
 * BlockNote d'utiliser nos custom blocks (slash menu, sérialisation, render).
 */
export const qgisBlockNoteSchema = BlockNoteSchema.create({
  blockSpecs: {
    ...defaultBlockSpecs,
    kpiGrid: KpiGridBlock,
    customHeading: CustomHeadingBlock,
    customQuote: CustomQuoteBlock,
    separator: SeparatorBlock,
    kpiBadge: KpiBadgeBlock,
    legend: LegendBlock,
    narrativeText: NarrativeTextBlock,
  },
});

export type QgisBlockNoteSchema = typeof qgisBlockNoteSchema;

/**
 * Convertit un ComponentKind hub en BlockNote block type.
 * Utilisé par le sérialiseur Assembly -> BlockNote document.
 */
export function componentKindToBlockType(kind: string): string | null {
  const mapping: Record<string, string> = {
    kpi_grid: 'kpiGrid',
    heading: 'customHeading',
    quote: 'customQuote',
    separator: 'separator',
    kpi_badge: 'kpiBadge',
    legend: 'legend',
    narrative_text: 'narrativeText',
    // F4+F5 à venir :
    // interactive_map: 'interactiveMap',
    // chart: 'chart',
    // data_table: 'dataTable',
    // scene_3d: 'scene3d',
    // media_embed: 'mediaEmbed',
    // iframe_grist: 'iframeGrist',
  };
  return mapping[kind] || null;
}
