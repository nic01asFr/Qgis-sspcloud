/**
 * Form fields communs réutilisés par les 7 forms d'édition.
 * Styles DSFR-aligned (police Marianne, couleurs #000091).
 */
import type { ReactNode } from 'react';

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 12,
  fontWeight: 600,
  color: '#3a3a3a',
  marginBottom: 4,
  textTransform: 'uppercase',
  letterSpacing: 0.5,
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  fontSize: 13,
  border: '1px solid #ccc',
  borderRadius: 4,
  fontFamily: 'inherit',
  boxSizing: 'border-box',
};

const fieldGroup: React.CSSProperties = {
  marginBottom: 16,
};

export function TextField({
  label,
  value,
  onChange,
  placeholder,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <div style={fieldGroup}>
      <label style={labelStyle}>{label}</label>
      <input
        type="text"
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={inputStyle}
      />
      {hint && (
        <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>{hint}</div>
      )}
    </div>
  );
}

export function TextareaField({
  label,
  value,
  onChange,
  rows = 4,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  hint?: string;
}) {
  return (
    <div style={fieldGroup}>
      <label style={labelStyle}>{label}</label>
      <textarea
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        style={{ ...inputStyle, fontFamily: 'monospace', resize: 'vertical' }}
      />
      {hint && (
        <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>{hint}</div>
      )}
    </div>
  );
}

export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  hint,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
}) {
  return (
    <div style={fieldGroup}>
      <label style={labelStyle}>{label}</label>
      <input
        type="number"
        value={value ?? 0}
        onChange={(e) => onChange(Number(e.target.value) || 0)}
        min={min}
        max={max}
        step={step}
        style={inputStyle}
      />
      {hint && (
        <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>{hint}</div>
      )}
    </div>
  );
}

export function SelectField({
  label,
  value,
  onChange,
  options,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: Array<{ value: string; label: string }>;
  hint?: string;
}) {
  return (
    <div style={fieldGroup}>
      <label style={labelStyle}>{label}</label>
      <select
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        style={inputStyle}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hint && (
        <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>{hint}</div>
      )}
    </div>
  );
}

export function ColorField({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
}) {
  return (
    <div style={fieldGroup}>
      <label style={labelStyle}>{label}</label>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input
          type="color"
          value={value || '#000091'}
          onChange={(e) => onChange(e.target.value)}
          style={{ width: 40, height: 36, border: '1px solid #ccc', borderRadius: 4, padding: 0 }}
        />
        <input
          type="text"
          value={value || '#000091'}
          onChange={(e) => onChange(e.target.value)}
          placeholder="#000091"
          style={{ ...inputStyle, flex: 1, fontFamily: 'monospace' }}
        />
      </div>
      {hint && (
        <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>{hint}</div>
      )}
    </div>
  );
}

export function FieldSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div style={{ marginBottom: 24 }}>
      <h3
        style={{
          fontSize: 13,
          fontWeight: 700,
          color: '#000091',
          margin: '0 0 12px',
          paddingBottom: 6,
          borderBottom: '2px solid #ececfe',
        }}
      >
        {title}
      </h3>
      {children}
    </div>
  );
}

export const DSFR_COLOR_OPTIONS = [
  { value: 'info-blue', label: 'Bleu Marianne (info-blue)' },
  { value: 'marianne-red', label: 'Rouge Marianne' },
  { value: 'success-green', label: 'Vert (success)' },
  { value: 'warning-orange', label: 'Orange (warning)' },
];
