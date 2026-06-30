/**
 * SymbologyFieldset — Section "Symbologie" per-layer du InteractiveMapForm V1.13.
 *
 * Sprint 1 V1.13 P0b-2 (D-QGIS-011 binding complet carto).
 *
 * Marie peut definir une classification thematique par layer :
 * - field : attribut numerique/categorical du GeoJSON
 * - method : jenks (defaut) / quantile / equal_interval / manual / categorized
 * - n_classes : 2-9 (defaut 5)
 * - palette : 6 palettes ColorBrewer (Blues/Reds/Greens/RdBu/RdYlGn/OrRd)
 *
 * Le helper hub `compute_classification` consomme ces params et genere
 * paint_expression MapLibre + breaks + colors + labels auto.
 *
 * Note : sub-component utilise dans LayersFieldset (un Symbology par layer
 * card). Pas appele directement par InteractiveMapForm.
 */
import { SelectField, NumberField, TextField } from '../fields';

export type ClassificationConfig = {
  field: string;
  method?: 'jenks' | 'quantile' | 'equal_interval' | 'manual' | 'categorized';
  n_classes?: number;
  palette?: 'Blues' | 'Reds' | 'Greens' | 'RdBu' | 'RdYlGn' | 'OrRd';
  breaks_manual?: number[];
  labels_override?: string[];
};

const METHOD_OPTIONS = [
  { value: 'jenks', label: 'Jenks natural breaks (defaut)' },
  { value: 'quantile', label: 'Quantiles (egale population)' },
  { value: 'equal_interval', label: 'Intervalles egaux' },
  { value: 'manual', label: 'Breaks manuels (CSV)' },
  { value: 'categorized', label: 'Categorise (valeurs discretes)' },
];

const PALETTE_OPTIONS = [
  { value: 'Blues', label: 'Blues (sequentielle bleue, defaut)' },
  { value: 'Reds', label: 'Reds (sequentielle rouge, alerte)' },
  { value: 'Greens', label: 'Greens (sequentielle verte, OK)' },
  { value: 'RdBu', label: 'RdBu (divergente rouge-bleu)' },
  { value: 'RdYlGn', label: 'RdYlGn (divergente vulnerabilite)' },
  { value: 'OrRd', label: 'OrRd (sequentielle orange-rouge, chaleur)' },
];

const swatchStyle = (color: string): React.CSSProperties => ({
  width: 18,
  height: 18,
  background: color,
  border: '1px solid #ccc',
  borderRadius: 2,
});

const palettePreview: Record<string, string[]> = {
  Blues: ['#eff3ff', '#bdd7e7', '#6baed6', '#3182bd', '#08519c'],
  Reds: ['#fee5d9', '#fcae91', '#fb6a4a', '#de2d26', '#a50f15'],
  Greens: ['#edf8e9', '#bae4b3', '#74c476', '#31a354', '#006d2c'],
  RdBu: ['#ca0020', '#f4a582', '#f7f7f7', '#92c5de', '#0571b0'],
  RdYlGn: ['#d7191c', '#fdae61', '#ffffbf', '#a6d96a', '#1a9641'],
  OrRd: ['#fef0d9', '#fdcc8a', '#fc8d59', '#e34a33', '#b30000'],
};

export function SymbologyFieldset({
  classif,
  propertiesKeys,
  onChange,
  onRemove,
}: {
  classif: ClassificationConfig | undefined;
  propertiesKeys: string[];
  onChange: (c: ClassificationConfig) => void;
  onRemove: () => void;
}) {
  if (!classif) {
    return (
      <div style={{ marginTop: 12, marginBottom: 12 }}>
        <button
          type="button"
          onClick={() => onChange({ field: propertiesKeys[0] || '', method: 'jenks', n_classes: 5, palette: 'Blues' })}
          style={{
            padding: '6px 12px',
            fontSize: 12,
            background: '#fff',
            border: '1px dashed #000091',
            borderRadius: 4,
            color: '#000091',
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          + Activer la symbologie thematique
        </button>
      </div>
    );
  }

  const field = classif.field || '';
  const method = classif.method || 'jenks';
  const n_classes = classif.n_classes || 5;
  const palette = classif.palette || 'Blues';

  // Field options : si on a les properties_keys, on les expose en SelectField
  const fieldOptions = propertiesKeys.map((k) => ({ value: k, label: k }));

  return (
    <div
      style={{
        marginTop: 12,
        padding: 10,
        background: '#fff',
        border: '1px solid #ccc',
        borderRadius: 4,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <strong style={{ fontSize: 12, color: '#000091' }}>SYMBOLOGIE</strong>
        <button
          type="button"
          onClick={onRemove}
          style={{
            fontSize: 11,
            background: 'transparent',
            border: 'none',
            color: '#a01010',
            cursor: 'pointer',
            textDecoration: 'underline',
          }}
        >
          Retirer
        </button>
      </div>

      {fieldOptions.length > 0 ? (
        <SelectField
          label="Attribut a classifier"
          value={field}
          onChange={(v) => onChange({ ...classif, field: v })}
          options={fieldOptions}
          hint="Choisir l'attribut numerique (Jenks/quantile) ou categoriel (categorized) qui pilote la couleur."
        />
      ) : (
        <TextField
          label="Attribut a classifier"
          value={field}
          onChange={(v) => onChange({ ...classif, field: v })}
          placeholder="risk_score, hauteur, usage_1..."
          hint="Properties key du GeoJSON (saisie libre si non detectees automatiquement)."
        />
      )}

      <SelectField
        label="Methode"
        value={method}
        onChange={(v) => onChange({ ...classif, method: v as any })}
        options={METHOD_OPTIONS}
      />

      {method !== 'categorized' && (
        <NumberField
          label="Nombre de classes"
          value={n_classes}
          onChange={(v) => onChange({ ...classif, n_classes: v })}
          min={2}
          max={9}
          step={1}
        />
      )}

      <SelectField
        label="Palette ColorBrewer"
        value={palette}
        onChange={(v) => onChange({ ...classif, palette: v as any })}
        options={PALETTE_OPTIONS}
      />

      {/* Palette preview */}
      <div style={{ display: 'flex', gap: 2, marginTop: 4, marginBottom: 12 }}>
        {(palettePreview[palette] || []).map((c, i) => (
          <div key={i} style={swatchStyle(c)} title={c} />
        ))}
      </div>

      {method === 'manual' && (
        <TextField
          label="Breaks manuels (CSV)"
          value={(classif.breaks_manual || []).join(',')}
          onChange={(v) => {
            const arr = v
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean)
              .map(Number)
              .filter((n) => !isNaN(n));
            onChange({ ...classif, breaks_manual: arr.length ? arr : undefined });
          }}
          placeholder="0, 5, 10, 20, 50"
          hint="Necessaire si methode=manual. Doit avoir n_classes+1 valeurs."
        />
      )}
    </div>
  );
}
