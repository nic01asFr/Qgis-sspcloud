/**
 * InteractionsFieldset - Section "Interactions" per-layer du InteractiveMapForm.
 *
 * Sprint 1 V1.13 P0b-2 (D-QGIS-011 binding complet carto).
 *
 * Chantier 2 V1.20.2 (2026-07-06) : refonte selecteur hover attributes
 * - fuzzy search input (filtre les attrs par nom)
 * - groupes categorises (Identifiants / Adresse / Bati / Altimetrie /
 *   Metadonnees / Autres) avec compteur selectionnes par groupe
 * - reduit charge cognitive Marie sur BD TOPO batiments 30+ attrs.
 *
 * Marie peut configurer le comportement interactif du layer :
 * - tooltip_field : attribut unique a afficher au survol (quick label)
 * - hover_attributes : whitelist d'attributs au survol (tooltip etendu)
 * - popup_template : HTML au clic avec placeholders {{ feature.properties.X }}
 *
 * Escape par defaut cote template ; opt-in raw via {{{ ... }}}.
 */
import { useMemo, useState } from 'react';
import { SelectField, TextareaField } from '../fields';

export type InteractionsConfig = {
  tooltip_field?: string;
  hover_attributes?: string[];
  popup_template?: string;
};

/**
 * Chantier 2 V1.20.2 : categorisation heuristique par mots-cles BD TOPO.
 * Ordre = priorite (un attribut matche le premier groupe qui match, sinon
 * "Autres").
 */
const CATEGORIES: Array<{
  id: string;
  label: string;
  emoji?: string;
  match: (key: string) => boolean;
}> = [
  {
    id: 'identity',
    label: 'Identifiants',
    match: (k) =>
      /^(id|cleabs|gml_id|fid|uuid)$/i.test(k) ||
      k.startsWith('identifiants_') ||
      k.startsWith('appariement_'),
  },
  {
    id: 'address',
    label: 'Adresse & commune',
    match: (k) =>
      /adresse|numero_voie|nom_voie|code_postal|commune|insee/i.test(k),
  },
  {
    id: 'batiment',
    label: 'Batiment',
    match: (k) =>
      /hauteur|etage|logement|nature|usage|materiau|construction|origine/i.test(
        k,
      ),
  },
  {
    id: 'altimetry',
    label: 'Altimetrie',
    match: (k) => k.startsWith('altitude_') || k.includes('altimetrique'),
  },
  {
    id: 'metadata',
    label: 'Metadonnees',
    match: (k) =>
      k.startsWith('date_') ||
      k === 'sources' ||
      k.includes('methode') ||
      k.includes('precision') ||
      k.includes('etat_de_l_objet') ||
      k.startsWith('appariement_'),
  },
];

function categorize(keys: string[]): Record<string, string[]> {
  const buckets: Record<string, string[]> = { other: [] };
  for (const cat of CATEGORIES) buckets[cat.id] = [];
  for (const key of keys) {
    const cat = CATEGORIES.find((c) => c.match(key));
    (buckets[cat ? cat.id : 'other'] as string[]).push(key);
  }
  return buckets;
}

function fuzzyMatch(needle: string, haystack: string): boolean {
  if (!needle) return true;
  const n = needle.toLowerCase();
  const h = haystack.toLowerCase();
  // Match direct + match fuzzy (chaque char de needle apparait dans l'ordre)
  if (h.includes(n)) return true;
  let idx = 0;
  for (const ch of h) {
    if (ch === n[idx]) idx++;
    if (idx === n.length) return true;
  }
  return false;
}

export function InteractionsFieldset({
  config,
  propertiesKeys,
  onChange,
}: {
  config: InteractionsConfig;
  propertiesKeys: string[];
  onChange: (c: InteractionsConfig) => void;
}) {
  const tooltip = config.tooltip_field || '';
  const hover = config.hover_attributes || [];
  const [search, setSearch] = useState('');
  const [openCategories, setOpenCategories] = useState<Set<string>>(
    () => new Set(['identity', 'address']),
  );

  const filtered = useMemo(
    () => propertiesKeys.filter((k) => fuzzyMatch(search, k)),
    [propertiesKeys, search],
  );

  const buckets = useMemo(() => categorize(filtered), [filtered]);

  const fieldOptions = [
    { value: '', label: '(aucun)' },
    ...propertiesKeys.map((k) => ({ value: k, label: k })),
  ];

  const toggleHoverAttr = (attr: string) => {
    const next = hover.includes(attr)
      ? hover.filter((a) => a !== attr)
      : [...hover, attr];
    onChange({ ...config, hover_attributes: next.length ? next : undefined });
  };

  const toggleCategory = (id: string) => {
    setOpenCategories((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const clearAll = () =>
    onChange({ ...config, hover_attributes: undefined });

  const selectAllInCategory = (keys: string[]) => {
    const next = Array.from(new Set([...hover, ...keys]));
    onChange({ ...config, hover_attributes: next });
  };

  return (
    <div
      style={{
        marginTop: 12,
        padding: 10,
        background: '#fff',
        border: '1px solid #ccc',
        borderRadius: 4,
      }}
    >
      <div style={{ marginBottom: 10 }}>
        <strong style={{ fontSize: 12, color: '#000091' }}>BULLES D'INFO</strong>
      </div>

      {fieldOptions.length > 1 ? (
        <SelectField
          label="Bulle rapide au survol"
          value={tooltip}
          onChange={(v) => onChange({ ...config, tooltip_field: v || undefined })}
          options={fieldOptions}
          hint="Affiche au passage de la souris une petite info (par exemple le nom de la rue)."
        />
      ) : (
        <div
          style={{
            fontSize: 11,
            color: '#888',
            fontStyle: 'italic',
            marginBottom: 12,
          }}
        >
          Aucun attribut detecte automatiquement. Configure ce layer via QGIS
          Desktop pour exposer ses attributs au form.
        </div>
      )}

      {propertiesKeys.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <label
            style={{
              display: 'block',
              fontSize: 12,
              fontWeight: 600,
              color: '#3a3a3a',
              marginBottom: 4,
              textTransform: 'uppercase',
              letterSpacing: 0.5,
            }}
          >
            Infos affichees au survol prolonge
          </label>
          <div
            style={{
              display: 'flex',
              gap: 8,
              marginBottom: 8,
              alignItems: 'center',
            }}
          >
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Rechercher un attribut…"
              aria-label="Filtrer les attributs par nom"
              style={{
                flex: 1,
                padding: '6px 10px',
                fontSize: 12,
                border: '1px solid #ccc',
                borderRadius: 4,
                fontFamily: 'inherit',
                boxSizing: 'border-box',
              }}
            />
            {hover.length > 0 && (
              <button
                type="button"
                onClick={clearAll}
                title="Retirer tous les attributs"
                style={{
                  padding: '5px 10px',
                  fontSize: 11,
                  background: '#fff',
                  color: '#a01010',
                  border: '1px solid #f4a8a8',
                  borderRadius: 4,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                  whiteSpace: 'nowrap',
                }}
              >
                Retirer tout ({hover.length})
              </button>
            )}
          </div>

          <div
            style={{
              background: '#fafafa',
              border: '1px solid #e5e5e5',
              borderRadius: 4,
              maxHeight: 260,
              overflow: 'auto',
            }}
          >
            {[...CATEGORIES, { id: 'other', label: 'Autres' }].map((cat) => {
              const keys = buckets[cat.id] || [];
              if (keys.length === 0) return null;
              const selectedInCat = keys.filter((k) => hover.includes(k));
              const isOpen = openCategories.has(cat.id) || search.length > 0;
              return (
                <div
                  key={cat.id}
                  style={{
                    borderBottom: '1px solid #eee',
                  }}
                >
                  <div
                    onClick={() => toggleCategory(cat.id)}
                    role="button"
                    aria-expanded={isOpen}
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        toggleCategory(cat.id);
                      }
                    }}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      padding: '6px 10px',
                      background: '#fff',
                      cursor: 'pointer',
                      userSelect: 'none',
                    }}
                  >
                    <span
                      aria-hidden="true"
                      style={{
                        display: 'inline-block',
                        transform: isOpen ? 'rotate(90deg)' : 'rotate(0deg)',
                        transition: 'transform 120ms',
                        color: '#000091',
                        fontSize: 10,
                        width: 10,
                      }}
                    >
                      ▶
                    </span>
                    <strong
                      style={{ fontSize: 11, color: '#3a3a3a', flex: 1 }}
                    >
                      {cat.label}
                    </strong>
                    <span
                      style={{
                        fontSize: 10,
                        color: selectedInCat.length > 0 ? '#000091' : '#888',
                        fontWeight: selectedInCat.length > 0 ? 600 : 400,
                      }}
                    >
                      {selectedInCat.length > 0
                        ? `${selectedInCat.length}/${keys.length}`
                        : keys.length}
                    </span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        selectAllInCategory(keys);
                      }}
                      title="Tout selectionner dans cette categorie"
                      style={{
                        padding: '2px 6px',
                        fontSize: 10,
                        background: '#fff',
                        border: '1px solid #ccc',
                        borderRadius: 3,
                        cursor: 'pointer',
                        fontFamily: 'inherit',
                        color: '#000091',
                      }}
                    >
                      + tous
                    </button>
                  </div>
                  {isOpen && (
                    <div
                      style={{
                        display: 'flex',
                        flexWrap: 'wrap',
                        gap: 4,
                        padding: '6px 10px 8px',
                      }}
                    >
                      {keys.map((k) => {
                        const checked = hover.includes(k);
                        return (
                          <label
                            key={k}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 4,
                              fontSize: 11,
                              padding: '2px 8px',
                              background: checked ? '#000091' : '#fff',
                              color: checked ? '#fff' : '#3a3a3a',
                              border: '1px solid #ccc',
                              borderRadius: 10,
                              cursor: 'pointer',
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleHoverAttr(k)}
                              style={{ marginRight: 4 }}
                            />
                            {k}
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>
            Tooltip etendu au survol prolonge. Vide = pas de tooltip etendu.
          </div>
        </div>
      )}

      <TextareaField
        label="Details affiches au clic (avance - HTML)"
        value={config.popup_template || ''}
        onChange={(v) =>
          onChange({ ...config, popup_template: v || undefined })
        }
        rows={4}
        hint="Editeur visuel a venir. En attendant : HTML avec placeholders {{ feature.properties.X }}. Vide = pas de details au clic."
      />
    </div>
  );
}
