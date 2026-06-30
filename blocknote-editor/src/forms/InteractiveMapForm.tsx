/**
 * InteractiveMapForm — édition du composant interactive_map (CLE METIER CEREMA).
 *
 * v1.12.0 : Marie peut configurer la carte MapLibre sans passer par
 * l'agent IA ou la modal JSON. Le composant carte est central dans
 * les storymaps CEREMA (risque inondation, exposition, etc.) donc
 * il doit être soigné.
 *
 * Configuration exposée (V1) :
 * - Titre / sous-titre carte (méta affichée en haut iframe)
 * - Description / contexte (1-2 phrases au-dessus de la carte)
 * - Caveat (mention prudence, encadré orange en bas)
 * - Source (citation Strate alignée)
 * - Fond de carte (basemap_id : 6 catalogues officiels)
 * - Hauteur (320-800px)
 *
 * V2 (différé) : édition des layers (symbology, classification, popups,
 * interactions). Demande UI plus complexe avec sub-panels par layer.
 */
import { TextField, TextareaField, SelectField, NumberField, FieldSection } from './fields';

const BASEMAP_OPTIONS = [
  { value: 'osm', label: 'OpenStreetMap (default)' },
  { value: 'plan-ign-v2', label: 'Plan IGN v2 (cartographie officielle FR)' },
  { value: 'ortho-ign', label: 'Photo aérienne IGN (orthophoto BD ORTHO)' },
  { value: 'dsfr-sobre', label: 'DSFR sobre (fond gris minimaliste)' },
  { value: 'hillshade-ign', label: 'Relief ombré IGN (terrain)' },
  { value: 'etalab', label: 'OSM Etalab (style fr.gouv)' },
];

export function InteractiveMapForm({
  data,
  onChange,
}: {
  data: Record<string, any>;
  onChange: (newData: Record<string, any>) => void;
}) {
  return (
    <>
      <FieldSection title="Titre et description">
        <TextField
          label="Titre de la carte"
          value={data.title || ''}
          onChange={(v) => onChange({ ...data, title: v })}
          placeholder="Bâtiments BD TOPO 4e arrondissement"
        />
        <TextField
          label="Sous-titre (optionnel)"
          value={data.subtitle || ''}
          onChange={(v) => onChange({ ...data, subtitle: v })}
          placeholder="14 270 bâtiments — couche IGN"
        />
        <TextareaField
          label="Description / contexte"
          value={data.description || ''}
          onChange={(v) => onChange({ ...data, description: v })}
          rows={3}
          hint="Affiché au-dessus de la carte. Décrit ce que la carte représente."
        />
      </FieldSection>

      <FieldSection title="Apparence">
        <SelectField
          label="Fond de carte"
          value={data.basemap_id || 'osm'}
          onChange={(v) => onChange({ ...data, basemap_id: v })}
          options={BASEMAP_OPTIONS}
          hint="Le fond influence fortement la lisibilité métier. Plan IGN v2 = officiel français, OSM = repère universel, DSFR sobre = focus sur les données métier."
        />
        <NumberField
          label="Hauteur (px)"
          value={data.height || 520}
          onChange={(v) => onChange({ ...data, height: v })}
          min={320}
          max={800}
          step={40}
          hint="Hauteur de la carte dans la storymap. 520px par défaut."
        />
      </FieldSection>

      <FieldSection title="Crédibilité (Source + Caveat)">
        <TextField
          label="Source Strate-aligned"
          value={data.source || ''}
          onChange={(v) => onChange({ ...data, source: v })}
          placeholder="BD TOPO IGN 2024"
          hint="Citation officielle de la donnée. Format Strate : corpus + millésime."
        />
        <TextareaField
          label="Caveat / mention prudence (optionnel)"
          value={data.caveat || ''}
          onChange={(v) => onChange({ ...data, caveat: v })}
          rows={2}
          hint="Encadré orange affiché EN BAS de la carte. Ex : 'Pour information uniquement, PPRi non opposable juridiquement.'"
        />
      </FieldSection>

      <FieldSection title="Avancé">
        <div
          style={{
            padding: 12,
            background: '#fff5e6',
            border: '1px solid #ffcb8c',
            borderRadius: 6,
            fontSize: 12,
            color: '#7a4400',
            lineHeight: 1.5,
          }}
        >
          <strong>Layers + symbologie + interactions :</strong> non éditables en
          V1.12. Pour modifier les couches (BD TOPO, TRI, etc.), la palette ou
          les popups, demande à l'agent IA via chat ou utilise la modal
          "🔧 Métadonnées" (mode JSON expert). Édition complète : Vague E3 V2.
        </div>
      </FieldSection>
    </>
  );
}
