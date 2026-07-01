/**
 * LayersFieldset — Section "Layers" du InteractiveMapForm V1.13.
 *
 * Sprint 1 V1.13 P0b-1 (D-QGIS-011 binding complet carto).
 *
 * Liste les layers du scene_manifest (fetch endpoint /source_layers) et
 * permet a Marie de :
 * - Toggler la visibilite par layer (visible: bool)
 * - Modifier l'opacite (opacity: 0-1)
 * - Renommer pour la legende (name_override)
 *
 * Sprint 1 V1.13 P0b-2 (a venir) ajoutera classification + popup_template
 * par layer dans ce meme fieldset (sub-form expand par layer).
 *
 * Le scene_manifest reste source de verite des donnees. layers_override
 * ne modifie QUE le rendu (visible/opacite/nom). L'utilisateur ne peut
 * pas ajouter/supprimer de layers depuis ce form (passe par QGIS Desktop
 * ou l'agent IA).
 */
import { useEffect, useState } from 'react';
import { TextField, NumberField, FieldSection } from '../fields';
import { SymbologyFieldset, type ClassificationConfig } from './SymbologyFieldset';
import { InteractionsFieldset, type InteractionsConfig } from './InteractionsFieldset';

export type SourceLayer = {
  id: string;
  name: string;
  geometry_type: string;
  n_features?: number;
  properties_keys: string[];
};

export type LayerOverride = {
  layer_id_ref: string;
  visible?: boolean;
  opacity?: number;
  name_override?: string;
  z_index?: number;
  // V1.13 P0b-2 : Symbology + Interactions per-layer
  classification?: ClassificationConfig;
  tooltip_field?: string;
  hover_attributes?: string[];
  popup_template?: string;
};

function fetchSourceLayers(
  sid: string,
  cid: string,
): Promise<{ layers: SourceLayer[]; scene_hash: string | null }> {
  return fetch(`/studies/${sid}/components/${cid}/source_layers`, {
    credentials: 'include',
  }).then((r) => {
    if (!r.ok) throw new Error(`source_layers ${r.status}`);
    return r.json();
  });
}

function findOverride(
  overrides: LayerOverride[],
  layer_id_ref: string,
): LayerOverride | undefined {
  return overrides.find((o) => o.layer_id_ref === layer_id_ref);
}

function applyOverride(
  overrides: LayerOverride[],
  layer_id_ref: string,
  patch: Partial<LayerOverride>,
): LayerOverride[] {
  const idx = overrides.findIndex((o) => o.layer_id_ref === layer_id_ref);
  if (idx >= 0) {
    const next = [...overrides];
    next[idx] = { ...next[idx], ...patch };
    return next;
  }
  return [...overrides, { layer_id_ref, ...patch }];
}

const layerCardStyle: React.CSSProperties = {
  border: '1px solid #ddd',
  borderRadius: 6,
  padding: 12,
  marginBottom: 8,
  background: '#fafafa',
};

const layerHeaderStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  marginBottom: 8,
  paddingBottom: 6,
  borderBottom: '1px solid #e5e5e5',
};

const badgeStyle: React.CSSProperties = {
  display: 'inline-block',
  padding: '2px 8px',
  background: '#000091',
  color: '#fff',
  borderRadius: 10,
  fontSize: 10,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: 0.3,
};

const checkboxLabel: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  fontSize: 12,
  color: '#3a3a3a',
  cursor: 'pointer',
};

export function LayersFieldset({
  sid,
  cid,
  layersOverride,
  onChange,
}: {
  sid: string | undefined;
  cid: string | undefined;
  layersOverride: LayerOverride[];
  onChange: (next: LayerOverride[]) => void;
}) {
  const [layers, setLayers] = useState<SourceLayer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!sid || !cid) return;
    setLoading(true);
    setError(null);
    // Sprint 1.5 V1.13.5 F2 : AbortController pour eviter race condition
    // (bug trouve test user Marie : erreur rouge stale affichee meme apres
    // fetch successful d'un re-mount sid/cid).
    const ctrl = new AbortController();
    fetch(`/studies/${sid}/components/${cid}/source_layers`, {
      credentials: 'include',
      signal: ctrl.signal,
    })
      .then((r) => {
        if (!r.ok) throw new Error(`source_layers ${r.status}`);
        return r.json();
      })
      .then((res) => {
        if (ctrl.signal.aborted) return;
        setLayers(res.layers);
        setError(null);
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        // F2 : seulement set error si pas d'AbortError
        if (e?.name !== 'AbortError') {
          setError(String(e?.message || e));
        }
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [sid, cid]);

  return (
    <FieldSection title={`Couches ${layers ? `(${layers.length})` : ''}`}>
      {loading && (
        <div style={{ fontSize: 12, color: '#666' }}>
          Chargement des layers du scene_manifest...
        </div>
      )}

      {error && (
        <div
          style={{
            padding: 10,
            background: '#fbebeb',
            border: '1px solid #f4a8a8',
            borderRadius: 6,
            fontSize: 12,
            color: '#a01010',
          }}
        >
          Impossible de lire les couches. La carte sera quand meme affichee,
          mais sans personnalisation possible des couches. Tu peux demander
          a l'assistant CEREMA de regenerer cette carte.
        </div>
      )}

      {layers && layers.length === 0 && (
        <div style={{ fontSize: 12, color: '#666', fontStyle: 'italic' }}>
          Aucune couche disponible. Utilise l'assistant CEREMA pour ajouter
          des couches au projet QGIS sous-jacent.
        </div>
      )}

      {layers && layers.length > 0 && (
        <>
          <div
            style={{
              fontSize: 11,
              color: '#666',
              marginBottom: 10,
              fontStyle: 'italic',
            }}
          >
            Les couches viennent de QGIS. Tu peux personnaliser leur visibilite,
            leur opacite et leur nom dans la legende.
          </div>

          {layers.map((layer) => {
            const ov = findOverride(layersOverride, layer.id) || {
              layer_id_ref: layer.id,
            };
            const visible = ov.visible !== false; // default true
            const opacity = ov.opacity ?? 1.0;

            return (
              <div key={layer.id} style={layerCardStyle}>
                <div style={layerHeaderStyle}>
                  <span style={badgeStyle}>{layer.geometry_type}</span>
                  <strong style={{ fontSize: 13, flex: 1 }}>
                    {ov.name_override || layer.name}
                  </strong>
                  {layer.n_features != null && (
                    <span style={{ fontSize: 11, color: '#666' }}>
                      {layer.n_features.toLocaleString('fr-FR')} objets
                    </span>
                  )}
                </div>

                <div style={{ display: 'flex', gap: 16, alignItems: 'center', marginBottom: 12 }}>
                  <label style={checkboxLabel}>
                    <input
                      type="checkbox"
                      checked={visible}
                      onChange={(e) =>
                        onChange(
                          applyOverride(layersOverride, layer.id, {
                            visible: e.target.checked,
                          }),
                        )
                      }
                    />
                    <span>Visible</span>
                  </label>
                </div>

                <NumberField
                  label="Opacite (0 = transparent, 1 = opaque)"
                  value={opacity}
                  onChange={(v) =>
                    onChange(
                      applyOverride(layersOverride, layer.id, { opacity: v }),
                    )
                  }
                  min={0}
                  max={1}
                  step={0.1}
                />

                <TextField
                  label="Nom dans la legende"
                  value={ov.name_override || ''}
                  onChange={(v) =>
                    onChange(
                      applyOverride(layersOverride, layer.id, {
                        name_override: v || undefined,
                      }),
                    )
                  }
                  placeholder={layer.name}
                  hint="Ce nom apparaitra dans la legende affichee sur la carte."
                />

                {/* V1.13 P0b-2 : Symbology per-layer */}
                <SymbologyFieldset
                  classif={ov.classification}
                  propertiesKeys={layer.properties_keys}
                  onChange={(c) =>
                    onChange(
                      applyOverride(layersOverride, layer.id, { classification: c }),
                    )
                  }
                  onRemove={() =>
                    onChange(
                      applyOverride(layersOverride, layer.id, {
                        classification: undefined,
                      }),
                    )
                  }
                />

                {/* V1.13 P0b-2 : Interactions per-layer */}
                <InteractionsFieldset
                  config={{
                    tooltip_field: ov.tooltip_field,
                    hover_attributes: ov.hover_attributes,
                    popup_template: ov.popup_template,
                  }}
                  propertiesKeys={layer.properties_keys}
                  onChange={(c) =>
                    onChange(
                      applyOverride(layersOverride, layer.id, {
                        tooltip_field: c.tooltip_field,
                        hover_attributes: c.hover_attributes,
                        popup_template: c.popup_template,
                      }),
                    )
                  }
                />
              </div>
            );
          })}

          {/* V1.17 : bascule sur onglet Assistant du panel unifie via bridge
              window.__switchPanelTab('assistant'). Fin du bridge V1.14.1
              window.__openAssistantWithPrompt (couple avec AssistantCard
              deprecate). */}
          <button
            type="button"
            onClick={() => {
              const fn = (window as any).__switchPanelTab;
              if (typeof fn === 'function') {
                fn('assistant');
              }
            }}
            style={{
              width: '100%',
              padding: '10px 14px',
              marginTop: 8,
              fontSize: 13,
              background: '#fff',
              border: '1px dashed #000091',
              borderRadius: 4,
              color: '#000091',
              cursor: 'pointer',
              fontFamily: 'inherit',
              fontWeight: 500,
            }}
          >
            + Ajouter une couche
          </button>
          <div
            style={{
              fontSize: 11,
              color: '#666',
              marginTop: 4,
              fontStyle: 'italic',
            }}
          >
            L'assistant CEREMA t'aidera a charger une nouvelle couche depuis
            les referentiels (BD TOPO, TRI, etc.).
          </div>
        </>
      )}
    </FieldSection>
  );
}
