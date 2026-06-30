import { TextField, ColorField, FieldSection } from './fields';

interface LegendItem {
  label: string;
  color: string;
}

export function LegendForm({
  data,
  onChange,
}: {
  data: Record<string, any>;
  onChange: (newData: Record<string, any>) => void;
}) {
  let items: LegendItem[] = [];
  try {
    items = typeof data.itemsJson === 'string' ? JSON.parse(data.itemsJson) : data.itemsJson || [];
  } catch {
    items = [];
  }

  const updateItem = (idx: number, patch: Partial<LegendItem>) => {
    const newList = items.map((it, i) => (i === idx ? { ...it, ...patch } : it));
    onChange({ ...data, itemsJson: JSON.stringify(newList) });
  };

  const addItem = () => {
    const newList = [...items, { label: 'Nouvel élément', color: '#000091' }];
    onChange({ ...data, itemsJson: JSON.stringify(newList) });
  };

  const removeItem = (idx: number) => {
    onChange({ ...data, itemsJson: JSON.stringify(items.filter((_, i) => i !== idx)) });
  };

  return (
    <>
      <FieldSection title="Métadonnées légende">
        <TextField
          label="Titre"
          value={data.title || ''}
          onChange={(v) => onChange({ ...data, title: v })}
          placeholder="Légende"
        />
        <TextField
          label="Source"
          value={data.source || ''}
          onChange={(v) => onChange({ ...data, source: v })}
          placeholder="BD TOPO IGN 2024"
        />
      </FieldSection>

      <FieldSection title={`Éléments (${items.length})`}>
        {items.map((item, idx) => (
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
            <button
              type="button"
              onClick={() => removeItem(idx)}
              title="Supprimer"
              style={{
                position: 'absolute',
                top: 6,
                right: 6,
                width: 22,
                height: 22,
                fontSize: 11,
                border: '1px solid #ccc',
                background: '#fff',
                borderRadius: 3,
                cursor: 'pointer',
                color: '#a50f15',
                padding: 0,
                lineHeight: 1,
              }}
            >
              ×
            </button>
            <div style={{ fontSize: 11, color: '#666', marginBottom: 8, fontWeight: 600 }}>
              Élément #{idx + 1}
            </div>
            <TextField
              label="Libellé"
              value={item.label || ''}
              onChange={(v) => updateItem(idx, { label: v })}
              placeholder="Zone TRI 100 ans"
            />
            <ColorField
              label="Couleur"
              value={item.color || '#000091'}
              onChange={(v) => updateItem(idx, { color: v })}
            />
          </div>
        ))}
        <button
          type="button"
          onClick={addItem}
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
          + Ajouter un élément
        </button>
      </FieldSection>
    </>
  );
}
