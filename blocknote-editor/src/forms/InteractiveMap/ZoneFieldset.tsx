/**
 * ZoneFieldset — Section "Zone d'etude" du InteractiveMapForm V1.13.
 *
 * Sprint 1 V1.13 P0b-1 (D-QGIS-011 binding complet carto).
 *
 * 3 modes (cf. hub.models.component_params.ZoneConfig) :
 * - commune : Marie choisit une commune INSEE + buffer_km
 *   Cote hub : _try_resolve_major_city resout INSEE -> bbox + center + zoom
 *   (KB project_bug_marseille_geocoding : piege Marseille->Marseillette).
 * - manual : Marie saisit center_lat/center_lng/zoom directement.
 * - study : Heritage automatique de la zone d'etude active
 *   (lookup studies.get_study(sid).zone cote hub).
 */
import { TextField, NumberField, FieldSection } from '../fields';

type ZoneKind = 'commune' | 'manual' | 'study';

export type ZoneConfig = {
  kind?: ZoneKind;
  insee?: string;
  buffer_km?: number;
  center_lat?: number;
  center_lng?: number;
  zoom?: number;
  bbox?: [number, number, number, number];
};

const tabStyle = (active: boolean): React.CSSProperties => ({
  flex: 1,
  padding: '8px 12px',
  background: active ? '#000091' : '#f5f5fe',
  color: active ? '#fff' : '#3a3a3a',
  border: '1px solid #ccc',
  borderRadius: 4,
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  textAlign: 'center' as const,
  textTransform: 'uppercase' as const,
  letterSpacing: 0.3,
  fontFamily: 'inherit',
});

export function ZoneFieldset({
  zone,
  onChange,
}: {
  zone: ZoneConfig;
  onChange: (z: ZoneConfig) => void;
}) {
  const kind: ZoneKind = zone?.kind || 'manual';

  return (
    <FieldSection title="Zone d'etude">
      <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
        <button
          type="button"
          style={tabStyle(kind === 'commune')}
          onClick={() => onChange({ ...zone, kind: 'commune' })}
        >
          Commune INSEE
        </button>
        <button
          type="button"
          style={tabStyle(kind === 'manual')}
          onClick={() => onChange({ ...zone, kind: 'manual' })}
        >
          Manuel
        </button>
        <button
          type="button"
          style={tabStyle(kind === 'study')}
          onClick={() => onChange({ ...zone, kind: 'study' })}
        >
          Etude active
        </button>
      </div>

      {kind === 'commune' && (
        <>
          <TextField
            label="Code INSEE"
            value={zone?.insee || ''}
            onChange={(v) => onChange({ ...zone, kind: 'commune', insee: v })}
            placeholder="13204 (Marseille 4e), 75104 (Paris 4e)..."
            hint="Code INSEE 5 chiffres. Pour Marseille/Paris/Lyon, utiliser l'arrondissement (KB project_bug_marseille_geocoding : 'Marseille' seul -> Marseillette Aude)."
          />
          <NumberField
            label="Buffer autour de la commune (km)"
            value={zone?.buffer_km || 0}
            onChange={(v) => onChange({ ...zone, kind: 'commune', buffer_km: v })}
            min={0}
            max={50}
            step={0.5}
            hint="0 = strict commune. 2 km = inclut le voisinage. Cote hub : bbox commune + buffer = zone affichee."
          />
        </>
      )}

      {kind === 'manual' && (
        <>
          <NumberField
            label="Latitude centre"
            value={zone?.center_lat || 43.30}
            onChange={(v) => onChange({ ...zone, kind: 'manual', center_lat: v })}
            min={-90}
            max={90}
            step={0.01}
            hint="EPSG:4326 (degres). Marseille 4e = 43.30, Paris = 48.85."
          />
          <NumberField
            label="Longitude centre"
            value={zone?.center_lng || 5.39}
            onChange={(v) => onChange({ ...zone, kind: 'manual', center_lng: v })}
            min={-180}
            max={180}
            step={0.01}
            hint="EPSG:4326 (degres). Marseille 4e = 5.39, Paris = 2.35."
          />
          <NumberField
            label="Zoom initial"
            value={zone?.zoom || 13}
            onChange={(v) => onChange({ ...zone, kind: 'manual', zoom: v })}
            min={0}
            max={22}
            step={0.5}
            hint="0 = monde. 13 = quartier. 16 = rue. 19 = batiment."
          />
        </>
      )}

      {kind === 'study' && (
        <div
          style={{
            padding: 12,
            background: '#e3f2fd',
            border: '1px solid #90caf9',
            borderRadius: 6,
            fontSize: 12,
            color: '#0d47a1',
            lineHeight: 1.5,
          }}
        >
          <strong>Heritage de l'etude active :</strong> la zone, le zoom et la bbox
          sont automatiquement lus depuis l'etude qgis-sspcloud (champ <code>study.zone</code>).
          Tout changement de la zone d'etude (via l'agent IA <code>set_study_zone</code>)
          se propage automatiquement a cette carte.
        </div>
      )}
    </FieldSection>
  );
}
