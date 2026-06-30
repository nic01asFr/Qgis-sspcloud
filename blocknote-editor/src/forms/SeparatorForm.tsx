import { SelectField, ColorField, FieldSection } from './fields';

export function SeparatorForm({
  data,
  onChange,
}: {
  data: Record<string, any>;
  onChange: (newData: Record<string, any>) => void;
}) {
  return (
    <FieldSection title="Séparateur">
      <SelectField
        label="Variante"
        value={data.variant || 'rule'}
        onChange={(v) => onChange({ ...data, variant: v })}
        options={[
          { value: 'rule', label: 'Trait pleine largeur (rule)' },
          { value: 'ornament', label: 'Trait court centré (ornament)' },
          { value: 'break', label: 'Espacement sans trait (break)' },
        ]}
      />
      <SelectField
        label="Style de ligne"
        value={data.style || 'solid'}
        onChange={(v) => onChange({ ...data, style: v })}
        options={[
          { value: 'solid', label: 'Continue' },
          { value: 'dashed', label: 'Tirets' },
          { value: 'dotted', label: 'Pointillés' },
        ]}
      />
      <ColorField
        label="Couleur"
        value={data.color || '#000091'}
        onChange={(v) => onChange({ ...data, color: v })}
        hint="Default : #000091 (bleu Marianne DSFR)."
      />
    </FieldSection>
  );
}
