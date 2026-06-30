import { TextField, SelectField, FieldSection } from './fields';

export function HeadingForm({
  data,
  onChange,
}: {
  data: Record<string, any>;
  onChange: (newData: Record<string, any>) => void;
}) {
  return (
    <FieldSection title="Titre">
      <TextField
        label="Texte du titre"
        value={data.text || ''}
        onChange={(v) => onChange({ ...data, text: v })}
        placeholder="Diagnostic risque inondation"
      />
      <SelectField
        label="Niveau"
        value={String(data.level || 2)}
        onChange={(v) => onChange({ ...data, level: Number(v) })}
        options={[
          { value: '1', label: 'H1 — Titre principal (32px)' },
          { value: '2', label: 'H2 — Section (26px)' },
          { value: '3', label: 'H3 — Sous-section (20px)' },
          { value: '4', label: 'H4 — Étiquette (16px)' },
        ]}
      />
    </FieldSection>
  );
}
