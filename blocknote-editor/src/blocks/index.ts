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
import {
  InteractiveMapBlock,
  ChartBlock,
  DataTableBlock,
  Scene3dBlock,
  MediaEmbedBlock,
  IframeGristBlock,
} from './IframeEmbed';

/**
 * Schema BlockNote avec 13 custom blocks Vague E2 :
 *  - 7 DOM atomiques (kpiGrid, customHeading, customQuote, separator,
 *                     kpiBadge, legend, narrativeText)
 *  - 6 iframe lourds (interactiveMap, chart, dataTable, scene3d,
 *                      mediaEmbed, iframeGrist)
 *
 * Couverture complète des ComponentKind hub.
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
    interactiveMap: InteractiveMapBlock,
    chart: ChartBlock,
    dataTable: DataTableBlock,
    scene3d: Scene3dBlock,
    mediaEmbed: MediaEmbedBlock,
    iframeGrist: IframeGristBlock,
  },
});

export type QgisBlockNoteSchema = typeof qgisBlockNoteSchema;

/**
 * Convertit un ComponentKind hub en BlockNote block type.
 * Couverture complète V1 = 13 kinds.
 */
export function componentKindToBlockType(kind: string): string | null {
  const mapping: Record<string, string> = {
    // DOM atomiques
    kpi_grid: 'kpiGrid',
    heading: 'customHeading',
    quote: 'customQuote',
    separator: 'separator',
    kpi_badge: 'kpiBadge',
    legend: 'legend',
    narrative_text: 'narrativeText',
    // Iframe lourds (Vague E2 Commit F4+F5)
    interactive_map: 'interactiveMap',
    chart: 'chart',
    data_table: 'dataTable',
    scene_3d: 'scene3d',
    media_embed: 'mediaEmbed',
    iframe_grist: 'iframeGrist',
  };
  return mapping[kind] || null;
}
