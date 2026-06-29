/**
 * Types TypeScript pour qgis-sspcloud Assembly + Component.
 *
 * V1 : types souples (dict[str, Any] côté Pydantic = Record<string, any> ici).
 * Future : générer depuis Pydantic schemas via datamodel-code-generator.
 *
 * Cf. ADR D-QGIS-010 + hub/hub/models/assembly.py + component.py.
 */

export type ComponentKind =
  | 'interactive_map'
  | 'scene_3d'
  | 'chart'
  | 'kpi_badge'
  | 'kpi_grid'
  | 'legend'
  | 'narrative_text'
  | 'data_table'
  | 'media_embed'
  | 'iframe_grist'
  | 'heading'
  | 'quote'
  | 'separator';

export type AssemblyKind =
  | 'storymap_narrative_dsfr'
  | 'dashboard'
  | 'sheet_a4'
  | 'modal_embed'
  | 'atlas_immersive';

export type Classification =
  | 'public'
  | 'cerema_internal'
  | 'restricted'
  | 'confidential';

export type SectionKind = 'intro' | 'section' | 'conclusion' | 'appendix';

export interface ComponentRef {
  ref: string; // cid 12 hex
}

export interface AssemblySection {
  kind: SectionKind;
  title?: string | null;
  narrative_md?: string | null;
  components: ComponentRef[];
}

export interface AssemblyLayout {
  type: 'scroll_vertical' | 'grid' | 'paginated' | 'tab' | 'fullscreen' | 'modal';
  sections: AssemblySection[];
}

export interface AssemblyFooter {
  sources?: Array<{ name: string; url?: string; license?: string }>;
  disclaimer?: string;
  mentions_legales?: string;
}

export interface AssemblyManifest {
  id?: string;
  kind: AssemblyKind;
  title: string;
  description?: string;
  audience: Classification;
  layout: AssemblyLayout;
  footer?: AssemblyFooter;
  version_num?: number;
  provenance?: Record<string, unknown>;
}

export interface ComponentManifest {
  id?: string;
  kind: ComponentKind;
  title: string;
  description?: string;
  classification: Classification;
  source?: Record<string, unknown>;
  params?: Record<string, unknown>;
  rendering?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
}

export interface AssemblyFetchResponse {
  metadata?: {
    aid: string;
    sid: string;
    owner: string;
    version_num: number;
    created_at: number;
    status: string;
  };
  manifest: AssemblyManifest;
  exists_on_pvc?: boolean;
}
