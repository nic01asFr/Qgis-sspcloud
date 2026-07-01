/**
 * InteractiveMapForm — edition du composant interactive_map (CLE METIER CEREMA).
 *
 * Sprint 1 V1.13 P0b-1 (D-QGIS-011 binding complet carto) : ajoute sections
 * Zone d'etude + Layers visibility/opacity/name_override (cf.
 * hub.models.component_params.InteractiveMapParams).
 *
 * Sprint 1 V1.13 P0b-2 (a venir) : Symbology per-layer + Interactions
 * (popup_template + tooltip_field + hover_attributes) dans le meme drawer.
 *
 * Architecture donnees :
 * - V1.12 OK : champs plats (title, subtitle, description, basemap_id,
 *   source, caveat, height) lus depuis block.props.
 * - V1.13 NEW : structure nestee (zone, layers_override) lue depuis
 *   block.props.* (peuplee par serializer.ts case 'interactive_map').
 * - Back-compat : hub.models.component_params.InteractiveMapParams.from_legacy_dict
 *   accepte les 2 formats (cle plate V1.12 ou structure V1.13).
 */
import { TextField, TextareaField, SelectField, NumberField, FieldSection } from './fields';
import { ZoneFieldset, type ZoneConfig } from './InteractiveMap/ZoneFieldset';
import { LayersFieldset, type LayerOverride } from './InteractiveMap/LayersFieldset';
import { DatasourceAutocomplete } from './InteractiveMap/DatasourceAutocomplete';
import { AssistantCard } from './InteractiveMap/AssistantCard';

const BASEMAP_OPTIONS = [
  { value: 'osm', label: 'OpenStreetMap (default)' },
  { value: 'plan-ign-v2', label: 'Plan IGN v2 (cartographie officielle FR)' },
  { value: 'ortho-ign', label: 'Photo aerienne IGN (orthophoto BD ORTHO)' },
  { value: 'dsfr-sobre', label: 'DSFR sobre (fond gris minimaliste)' },
  { value: 'hillshade-ign', label: 'Relief ombre IGN (terrain)' },
  { value: 'etalab', label: 'OSM Etalab (style fr.gouv)' },
];

function getSidFromUrl(): string | undefined {
  // L'editeur est servi sous /editor/{sid}/assembly/{aid} ou similaire.
  // On extrait le sid pour fetch /studies/{sid}/components/{cid}/source_layers.
  try {
    const path = window.location.pathname;
    const match = path.match(/\/editor\/([0-9a-f]{12})\//);
    return match ? match[1] : undefined;
  } catch {
    return undefined;
  }
}

export function InteractiveMapForm({
  data,
  onChange,
}: {
  data: Record<string, any>;
  onChange: (newData: Record<string, any>) => void;
}) {
  const sid = getSidFromUrl();
  const cid: string | undefined = data.cid;

  // Zone d'etude V1.13 : data.zone (nested) ou fallback champs plats V1.12
  const zone: ZoneConfig = data.zone || {
    kind: 'manual',
    center_lat: data.center_lat,
    center_lng: data.center_lng,
    zoom: data.zoom,
  };

  // Layers override V1.13 : array de LayerOverride
  const layersOverride: LayerOverride[] = Array.isArray(data.layers_override)
    ? data.layers_override
    : [];

  return (
    <>
      {/* V1.13.5 Sprint 1.5 F7 : reordonner sections selon logique metier Marie
          (Zone → Couches → Apparence → Titre → Sources, vs V1.13 ordre Pydantic) */}

      {/* Sprint 2.5 V2.5 A2d : AssistantCard en HAUT du drawer (Notion-like) */}
      <AssistantCard sid={sid} cid={cid} />

      <ZoneFieldset
        zone={zone}
        onChange={(z) => onChange({ ...data, zone: z })}
      />

      <LayersFieldset
        sid={sid}
        cid={cid}
        layersOverride={layersOverride}
        onChange={(next) => onChange({ ...data, layers_override: next })}
      />

      <FieldSection title="Apparence">
        <SelectField
          label="Fond de carte"
          value={data.basemap_id || 'osm'}
          onChange={(v) => onChange({ ...data, basemap_id: v })}
          options={BASEMAP_OPTIONS}
          hint="Le fond influence la lisibilite metier. Plan IGN v2 = officiel francais, OSM = repere universel, DSFR sobre = focus sur les donnees metier."
        />
        <NumberField
          label="Hauteur (px)"
          value={data.height || 520}
          onChange={(v) => onChange({ ...data, height: v })}
          min={320}
          max={800}
          step={40}
          hint="Hauteur de la carte dans la storymap. 520px par defaut."
        />
      </FieldSection>

      <FieldSection title="Titre et description">
        <TextField
          label="Titre de la carte"
          value={data.title || ''}
          onChange={(v) => onChange({ ...data, title: v })}
          placeholder="Batiments BD TOPO 4e arrondissement"
        />
        <TextField
          label="Sous-titre (optionnel)"
          value={data.subtitle || ''}
          onChange={(v) => onChange({ ...data, subtitle: v })}
          placeholder="14 270 batiments — couche IGN"
        />
        <TextareaField
          label="Description / contexte"
          value={data.description || ''}
          onChange={(v) => onChange({ ...data, description: v })}
          rows={3}
          hint="Affichee au-dessus de la carte. Decrit ce que la carte represente."
        />
      </FieldSection>

      <FieldSection title="Source et avertissement">
        <DatasourceAutocomplete
          datasourceId={data.datasource_id || undefined}
          sourceFreeText={data.source || ''}
          onChangeDatasourceId={(id) => onChange({ ...data, datasource_id: id })}
          onChangeSource={(v) => onChange({ ...data, source: v })}
        />
        <TextareaField
          label="Avertissement / mention prudence (optionnel)"
          value={data.caveat || ''}
          onChange={(v) => onChange({ ...data, caveat: v })}
          rows={2}
          hint="Encadre orange affiche EN BAS de la carte. Ex : 'Pour information uniquement, PPRi non opposable juridiquement.'"
        />
      </FieldSection>

      {/* V1.13.5 F1 : section "Avance" SUPPRIMEE (etait desinformante, annoncait
          comme "a venir" des features deja livrees en P0b-2). */}
    </>
  );
}
