/**
 * InteractionsFieldset — Section "Interactions" per-layer du InteractiveMapForm V1.13.
 *
 * Sprint 1 V1.13 P0b-2 (D-QGIS-011 binding complet carto).
 *
 * Marie peut configurer le comportement interactif du layer :
 * - tooltip_field : attribut unique a afficher au survol (quick label)
 * - hover_attributes : whitelist d'attributs au survol (tooltip etendu)
 * - popup_template : HTML au clic avec placeholders {{ feature.properties.X }}
 *
 * Sub-component utilise dans LayersFieldset (un Interactions par layer card).
 * Escape par defaut cote template ; opt-in raw via {{{ ... }}}.
 */
import { SelectField, TextareaField } from '../fields';

export type InteractionsConfig = {
  tooltip_field?: string;
  hover_attributes?: string[];
  popup_template?: string;
};

export function InteractionsFieldset({
  config,
  propertiesKeys,
  onChange,
}: {
  config: InteractionsConfig;
  propertiesKeys: string[];
  onChange: (c: InteractionsConfig) => void;
}) {
  const tooltip = config.tooltip_field || '';
  const hover = config.hover_attributes || [];

  const fieldOptions = [
    { value: '', label: '(aucun)' },
    ...propertiesKeys.map((k) => ({ value: k, label: k })),
  ];

  const toggleHoverAttr = (attr: string) => {
    const next = hover.includes(attr)
      ? hover.filter((a) => a !== attr)
      : [...hover, attr];
    onChange({ ...config, hover_attributes: next.length ? next : undefined });
  };

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
      <div style={{ marginBottom: 10 }}>
        <strong style={{ fontSize: 12, color: '#000091' }}>INTERACTIONS</strong>
      </div>

      {fieldOptions.length > 1 ? (
        <SelectField
          label="Tooltip rapide au survol (1 attribut)"
          value={tooltip}
          onChange={(v) => onChange({ ...config, tooltip_field: v || undefined })}
          options={fieldOptions}
          hint="Affiche immediatement au survol de la souris (style label leger)."
        />
      ) : (
        <div
          style={{
            fontSize: 11,
            color: '#888',
            fontStyle: 'italic',
            marginBottom: 12,
          }}
        >
          Aucun attribut detecte automatiquement. Configure ce layer via QGIS
          Desktop pour exposer ses attributs au form.
        </div>
      )}

      {propertiesKeys.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <label
            style={{
              display: 'block',
              fontSize: 12,
              fontWeight: 600,
              color: '#3a3a3a',
              marginBottom: 4,
              textTransform: 'uppercase',
              letterSpacing: 0.5,
            }}
          >
            Attributs au hover etendu
          </label>
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 6,
              padding: 8,
              background: '#fafafa',
              border: '1px solid #e5e5e5',
              borderRadius: 4,
              maxHeight: 120,
              overflow: 'auto',
            }}
          >
            {propertiesKeys.map((k) => {
              const checked = hover.includes(k);
              return (
                <label
                  key={k}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4,
                    fontSize: 11,
                    padding: '2px 8px',
                    background: checked ? '#000091' : '#fff',
                    color: checked ? '#fff' : '#3a3a3a',
                    border: '1px solid #ccc',
                    borderRadius: 10,
                    cursor: 'pointer',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleHoverAttr(k)}
                    style={{ marginRight: 4 }}
                  />
                  {k}
                </label>
              );
            })}
          </div>
          <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>
            Tooltip etendu au survol prolonge. Vide = pas de tooltip etendu.
          </div>
        </div>
      )}

      <TextareaField
        label="Popup au clic (template HTML)"
        value={config.popup_template || ''}
        onChange={(v) =>
          onChange({ ...config, popup_template: v || undefined })
        }
        rows={4}
        hint="HTML avec placeholders {{ feature.properties.X }}. Escape par defaut. Vide = pas de popup au clic. Ex : '<strong>{{ feature.properties.adresse }}</strong><br>Risque : {{ feature.properties.risk_score }}'"
      />
    </div>
  );
}
