/**
 * KpiGridForm — édition d'un bandeau N KPIs.
 *
 * Sprint 4 v1.10.0 (8.18 option A).
 */
import { TextField, NumberField, SelectField, FieldSection, DSFR_COLOR_OPTIONS } from './fields';

interface Kpi {
  value: string | number;
  label: string;
  unit?: string;
  color?: string;
}

export function KpiGridForm({
  data,
  onChange,
}: {
  data: Record<string, any>;
  onChange: (newData: Record<string, any>) => void;
}) {
  let kpis: Kpi[] = [];
  try {
    kpis = typeof data.kpisJson === 'string' ? JSON.parse(data.kpisJson) : data.kpisJson || [];
  } catch {
    kpis = [];
  }

  const updateKpi = (idx: number, patch: Partial<Kpi>) => {
    const newList = kpis.map((k, i) => (i === idx ? { ...k, ...patch } : k));
    onChange({ ...data, kpisJson: JSON.stringify(newList) });
  };

  const addKpi = () => {
    const newList = [
      ...kpis,
      { value: '0', label: 'Nouveau KPI', unit: '', color: 'info-blue' },
    ];
    onChange({ ...data, kpisJson: JSON.stringify(newList) });
  };

  const removeKpi = (idx: number) => {
    const newList = kpis.filter((_, i) => i !== idx);
    onChange({ ...data, kpisJson: JSON.stringify(newList) });
  };

  const moveKpi = (idx: number, dir: -1 | 1) => {
    const target = idx + dir;
    if (target < 0 || target >= kpis.length) return;
    const newList = [...kpis];
    [newList[idx], newList[target]] = [newList[target], newList[idx]];
    onChange({ ...data, kpisJson: JSON.stringify(newList) });
  };

  return (
    <>
      <FieldSection title="Apparence générale">
        <SelectField
          label="Palette"
          value={data.palette || 'monochrome'}
          onChange={(v) => onChange({ ...data, palette: v })}
          options={[
            { value: 'monochrome', label: 'Monochrome (dégradé bleu Marianne)' },
            { value: 'rainbow', label: 'Multicolore (couleur par KPI)' },
          ]}
          hint="Monochrome = dégradés bleus auto. Multicolore = utilise color par KPI."
        />
        <NumberField
          label="Largeur min des chips (px)"
          value={data.columnsMin || 140}
          onChange={(v) => onChange({ ...data, columnsMin: v })}
          min={80}
          max={400}
          step={20}
          hint="Plus petit = plus de KPIs par ligne. Default 140."
        />
      </FieldSection>

      <FieldSection title={`KPIs (${kpis.length})`}>
        {kpis.map((kpi, idx) => (
          <div
            key={idx}
            style={{
              padding: 12,
              background: '#f6f6f6',
              borderRadius: 6,
              marginBottom: 12,
              position: 'relative',
            }}
          >
            <div
              style={{
                position: 'absolute',
                top: 6,
                right: 6,
                display: 'flex',
                gap: 4,
              }}
            >
              <button
                type="button"
                onClick={() => moveKpi(idx, -1)}
                disabled={idx === 0}
                title="Monter"
                style={miniBtn}
              >
                ▲
              </button>
              <button
                type="button"
                onClick={() => moveKpi(idx, 1)}
                disabled={idx === kpis.length - 1}
                title="Descendre"
                style={miniBtn}
              >
                ▼
              </button>
              <button
                type="button"
                onClick={() => removeKpi(idx)}
                title="Supprimer"
                style={{ ...miniBtn, color: '#a50f15' }}
              >
                ×
              </button>
            </div>
            <div style={{ fontSize: 11, color: '#666', marginBottom: 8, fontWeight: 600 }}>
              KPI #{idx + 1}
            </div>
            <TextField
              label="Valeur"
              value={String(kpi.value || '')}
              onChange={(v) => updateKpi(idx, { value: v })}
              placeholder="47"
            />
            <TextField
              label="Libellé"
              value={kpi.label || ''}
              onChange={(v) => updateKpi(idx, { label: v })}
              placeholder="du territoire"
            />
            <TextField
              label="Unité (optionnel)"
              value={kpi.unit || ''}
              onChange={(v) => updateKpi(idx, { unit: v })}
              placeholder="%"
            />
            <SelectField
              label="Couleur"
              value={kpi.color || 'info-blue'}
              onChange={(v) => updateKpi(idx, { color: v })}
              options={DSFR_COLOR_OPTIONS}
            />
          </div>
        ))}
        <button
          type="button"
          onClick={addKpi}
          style={{
            width: '100%',
            padding: '8px 12px',
            fontSize: 13,
            border: '1px dashed #000091',
            background: '#fff',
            color: '#000091',
            borderRadius: 4,
            cursor: 'pointer',
            fontWeight: 600,
          }}
        >
          + Ajouter un KPI
        </button>
      </FieldSection>
    </>
  );
}

const miniBtn: React.CSSProperties = {
  width: 22,
  height: 22,
  fontSize: 11,
  border: '1px solid #ccc',
  background: '#fff',
  borderRadius: 3,
  cursor: 'pointer',
  padding: 0,
  lineHeight: 1,
};
