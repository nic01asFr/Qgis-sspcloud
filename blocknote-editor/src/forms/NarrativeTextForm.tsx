import { TextareaField, FieldSection } from './fields';

export function NarrativeTextForm({
  data,
  onChange,
}: {
  data: Record<string, any>;
  onChange: (newData: Record<string, any>) => void;
}) {
  return (
    <FieldSection title="Texte narratif (Markdown)">
      <TextareaField
        label="Contenu Markdown"
        value={data.content || ''}
        onChange={(v) => onChange({ ...data, content: v })}
        rows={10}
        hint="Markdown : **gras**, *italique*, [lien](url), - listes. Le rendu utilise Marked.js."
      />
    </FieldSection>
  );
}
