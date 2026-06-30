import { TextField, SelectField, FieldSection, DSFR_COLOR_OPTIONS } from './fields';

export function KpiBadgeForm({
  data,
  onChange,
}: {
  data: Record<string, any>;
  onChange: (newData: Record<string, any>) => void;
}) {
  return (
    <FieldSection title="KPI isolé">
      <TextField
        label="Valeur"
        value={data.value || ''}
        onChange={(v) => onChange({ ...data, value: v })}
        placeholder="47"
      />
      <TextField
        label="Libellé"
        value={data.label || ''}
        onChange={(v) => onChange({ ...data, label: v })}
        placeholder="% du territoire"
      />
      <TextField
        label="Unité (optionnel)"
        value={data.unit || ''}
        onChange={(v) => onChange({ ...data, unit: v })}
        placeholder="%"
      />
      <SelectField
        label="Couleur"
        value={data.color || 'info-blue'}
        onChange={(v) => onChange({ ...data, color: v })}
        options={DSFR_COLOR_OPTIONS}
      />
      <TextField
        label="Source (optionnel)"
        value={data.source || ''}
        onChange={(v) => onChange({ ...data, source: v })}
        placeholder="TRI Georisques 2026"
        hint="Citation affichée en bas du KPI"
      />
    </FieldSection>
  );
}
