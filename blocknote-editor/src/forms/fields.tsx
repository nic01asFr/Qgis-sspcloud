/**
 * Form fields communs réutilisés par les 7 forms d'édition.
 * Styles DSFR-aligned (police Marianne, couleurs #000091).
 *
 * Chantier 2 V1.20.2 (2026-07-06) : FieldSection devient collapsible
 * (accordion pattern) pour reduire la surcharge visuelle Marie (150+ champs
 * dans InteractiveMapForm). Backward compat : props optionnelles.
 */
import { useState, type ReactNode } from 'react';

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
  // Chantier 2 V1.20.2 : validation inline rouge/vert. Marie voit
  // immediatement quand une valeur sort des bornes (Latitude hors [-90,90],
  // Zoom > 22, etc.) au lieu d'un attribut HTML5 silencieux.
  const numValue = value ?? 0;
  const belowMin = min !== undefined && numValue < min;
  const aboveMax = max !== undefined && numValue > max;
  const invalid = belowMin || aboveMax;
  const errorMsg = belowMin
    ? `Valeur minimum : ${min}`
    : aboveMax
      ? `Valeur maximum : ${max}`
      : null;
  const style: React.CSSProperties = invalid
    ? { ...inputStyle, borderColor: '#ce0500', boxShadow: '0 0 0 1px #ce0500' }
    : inputStyle;
  return (
    <div style={fieldGroup}>
      <label style={labelStyle}>{label}</label>
      <input
        type="number"
        value={numValue}
        onChange={(e) => onChange(Number(e.target.value) || 0)}
        min={min}
        max={max}
        step={step}
        style={style}
        aria-invalid={invalid || undefined}
        aria-describedby={errorMsg ? `${label}-err` : undefined}
      />
      {errorMsg && (
        <div
          id={`${label}-err`}
          role="alert"
          style={{ fontSize: 11, color: '#ce0500', marginTop: 4, fontWeight: 500 }}
        >
          {errorMsg}
        </div>
      )}
      {!errorMsg && hint && (
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
  /**
   * Chantier 2 V1.20.2 : section pliable (defaut true). Marie evite le
   * scroll de 150 champs empiles. Backward compat : ancien appel = collapsible
   * par defaut open.
   */
  collapsible = true,
  defaultOpen = true,
  /** Compteur/status affiche a droite du titre (ex: "Couches (3)") */
  badge,
  /** Callback bouton "Configurer avec l'assistant" contextuel */
  onAgentAssist,
}: {
  title: string;
  children: ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
  badge?: ReactNode;
  onAgentAssist?: () => void;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const canToggle = collapsible;
  const showContent = !canToggle || isOpen;

  return (
    <div
      style={{
        marginBottom: 24,
        border: canToggle ? '1px solid #ececfe' : 'none',
        borderRadius: canToggle ? 6 : 0,
        background: canToggle ? '#fdfdff' : 'transparent',
        overflow: 'hidden',
      }}
    >
      <div
        onClick={canToggle ? () => setIsOpen((v) => !v) : undefined}
        role={canToggle ? 'button' : undefined}
        aria-expanded={canToggle ? isOpen : undefined}
        tabIndex={canToggle ? 0 : undefined}
        onKeyDown={
          canToggle
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setIsOpen((v) => !v);
                }
              }
            : undefined
        }
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          cursor: canToggle ? 'pointer' : 'default',
          padding: canToggle ? '10px 12px' : '0 0 6px',
          borderBottom: canToggle
            ? isOpen
              ? '1px solid #ececfe'
              : 'none'
            : '2px solid #ececfe',
          transition: 'background 120ms',
          userSelect: 'none',
        }}
      >
        {canToggle && (
          <span
            aria-hidden="true"
            style={{
              display: 'inline-block',
              width: 14,
              height: 14,
              lineHeight: '14px',
              textAlign: 'center',
              transform: isOpen ? 'rotate(90deg)' : 'rotate(0deg)',
              transition: 'transform 150ms',
              color: '#000091',
              fontSize: 12,
              fontWeight: 700,
            }}
          >
            ▶
          </span>
        )}
        <h3
          style={{
            fontSize: 13,
            fontWeight: 700,
            color: '#000091',
            margin: 0,
            flex: 1,
          }}
        >
          {title}
        </h3>
        {badge !== undefined && badge !== null && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 500,
              color: '#666',
              padding: '2px 8px',
              background: '#ececfe',
              borderRadius: 10,
            }}
          >
            {badge}
          </span>
        )}
        {onAgentAssist && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onAgentAssist();
            }}
            title="Configurer cette section avec l'assistant"
            aria-label="Configurer cette section avec l'assistant"
            style={{
              padding: '3px 8px',
              fontSize: 11,
              background: '#fff',
              border: '1px solid #000091',
              color: '#000091',
              borderRadius: 3,
              cursor: 'pointer',
              fontFamily: 'inherit',
              fontWeight: 600,
              whiteSpace: 'nowrap',
            }}
          >
            ⚡ Assistant
          </button>
        )}
      </div>
      {showContent && (
        <div style={{ padding: canToggle ? '12px' : '12px 0 0' }}>{children}</div>
      )}
    </div>
  );
}

export const DSFR_COLOR_OPTIONS = [
  { value: 'info-blue', label: 'Bleu Marianne (info-blue)' },
  { value: 'marianne-red', label: 'Rouge Marianne' },
  { value: 'success-green', label: 'Vert (success)' },
  { value: 'warning-orange', label: 'Orange (warning)' },
];
