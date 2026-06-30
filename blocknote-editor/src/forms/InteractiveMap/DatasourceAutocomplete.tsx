/**
 * DatasourceAutocomplete — Autocomplete catalogue Strate du form Marie.
 *
 * Sprint 1 V1.13 P0d (D-QGIS-011 binding complet carto).
 *
 * Avant : TextField libre `source: string` -> drift des citations
 * (Marie ecrivait "BD TOPO" / "bdtopo 2024" / "IGN BDTOPO" -> chaos).
 *
 * Apres : SelectField alimente par GET /catalog/datasources, qui :
 * - Liste 14+ sources Strate-aligned (IGN, DGPR, DGFiP, EEA, INSEE, CEREMA)
 * - Categorie filtrable (referentiel/risque/fiscalite/environnement/...)
 * - Auto-fill `params.source` avec la citation complete corpus + millesime
 *   + autorite + licence.
 *
 * Compat back : Marie peut toujours surcharger en TextField libre si la
 * source n'est pas au catalogue (champ texte libre conserve).
 */
import { useEffect, useState } from 'react';
import { TextField, SelectField } from '../fields';

export type Datasource = {
  id: string;
  label: string;
  short_label: string;
  authority: string;
  licence: string;
  category: string;
  url: string | null;
};

function fetchDatasources(): Promise<{ datasources: Datasource[]; categories: string[] }> {
  return fetch('/catalog/datasources', { credentials: 'include' }).then((r) => {
    if (!r.ok) throw new Error(`catalog/datasources ${r.status}`);
    return r.json();
  });
}

const categoryLabels: Record<string, string> = {
  referentiel: 'Referentiel (IGN BD TOPO, RGE ALTI, ...)',
  risque: 'Risque (Georisques, TRI, PPRi, ...)',
  fiscalite: 'Fiscalite (DVF DGFiP, ...)',
  environnement: 'Environnement (CORINE LC, OCS GE, ...)',
  demographie: 'Demographie (INSEE, ...)',
  mobilite: 'Mobilite (CEREMA, ...)',
};

export function DatasourceAutocomplete({
  datasourceId,
  sourceFreeText,
  onChangeDatasourceId,
  onChangeSource,
}: {
  datasourceId: string | undefined;
  sourceFreeText: string;
  onChangeDatasourceId: (id: string | undefined) => void;
  onChangeSource: (s: string) => void;
}) {
  const [datasources, setDatasources] = useState<Datasource[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState<string>('');

  useEffect(() => {
    fetchDatasources()
      .then((res) => setDatasources(res.datasources))
      .catch((e) => setError(String(e?.message || e)));
  }, []);

  const filtered = (datasources || []).filter(
    (d) => !category || d.category === category,
  );

  const options = [
    { value: '', label: '(choisir une source du catalogue ou saisir libre)' },
    ...filtered.map((d) => ({
      value: d.id,
      label: `${d.short_label} [${d.authority}]`,
    })),
  ];

  const categoryOptions = [
    { value: '', label: 'Toutes categories' },
    ...Object.keys(categoryLabels).map((k) => ({
      value: k,
      label: categoryLabels[k],
    })),
  ];

  // Quand Marie choisit un datasource_id, auto-fill source (label canonique)
  const handlePick = (id: string) => {
    if (!id) {
      onChangeDatasourceId(undefined);
      return;
    }
    onChangeDatasourceId(id);
    const ds = (datasources || []).find((d) => d.id === id);
    if (ds) {
      onChangeSource(ds.label);
    }
  };

  return (
    <>
      {error && (
        <div
          style={{
            padding: 8,
            fontSize: 11,
            background: '#fbebeb',
            border: '1px solid #f4a8a8',
            borderRadius: 4,
            color: '#a01010',
            marginBottom: 8,
          }}
        >
          Catalogue indisponible ({error}). Saisis la source en texte libre.
        </div>
      )}

      {datasources && (
        <>
          <SelectField
            label="Filtrer par categorie"
            value={category}
            onChange={setCategory}
            options={categoryOptions}
          />

          <SelectField
            label="Source du catalogue Strate"
            value={datasourceId || ''}
            onChange={handlePick}
            options={options}
            hint="Choisir une source du catalogue auto-remplit la citation. 14+ sources Strate-aligned disponibles (IGN, DGPR, DGFiP, ...)."
          />
        </>
      )}

      <TextField
        label="Source (citation complete)"
        value={sourceFreeText}
        onChange={onChangeSource}
        placeholder="BD TOPO IGN 2024"
        hint="Texte libre conserve. Tu peux override la citation auto si besoin (corpus + millesime)."
      />
    </>
  );
}
