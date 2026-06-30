import { TextField, TextareaField, FieldSection } from './fields';

export function QuoteForm({
  data,
  onChange,
}: {
  data: Record<string, any>;
  onChange: (newData: Record<string, any>) => void;
}) {
  return (
    <FieldSection title="Citation">
      <TextareaField
        label="Texte de la citation"
        value={data.text || ''}
        onChange={(v) => onChange({ ...data, text: v })}
        rows={5}
        hint="Sans guillemets — ils sont ajoutés automatiquement au rendu."
      />
      <TextField
        label="Auteur"
        value={data.author || ''}
        onChange={(v) => onChange({ ...data, author: v })}
        placeholder="CEREMA DTerMed"
      />
      <TextField
        label="Source / contexte"
        value={data.source || ''}
        onChange={(v) => onChange({ ...data, source: v })}
        placeholder="Diagnostic risque inondation 2026"
      />
    </FieldSection>
  );
}
