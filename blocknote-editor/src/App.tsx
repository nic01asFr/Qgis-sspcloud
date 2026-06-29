/**
 * Editeur BlockNote - App principal.
 *
 * Vague E2 Commit E2 (D-QGIS-010) : fetch assembly read-only + display
 * sections en blocks BlockNote natifs (paragraph/heading/list).
 *
 * Prochains commits :
 * - F1 : 1er custom block kpi_grid DOM (pattern de référence)
 * - F2-F5 : 12 autres custom blocks Vague E2
 * - G : sérialisation bi-dir Assembly <-> BlockNote JSON + tests round-trip
 * - H1-H3 : autosave + concurrency + integration desk + tag v1.7.0
 */
import { useEffect, useState } from 'react';
import { useCreateBlockNote } from '@blocknote/react';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/mantine/style.css';
import { fetchAssembly } from './api';
import type { AssemblyFetchResponse, AssemblySection } from './types';

/**
 * Parse sid/aid depuis l'URL path /editor/{sid}/assembly/{aid}.
 */
function parseRouteParams(): { sid: string; aid: string } {
  const match = window.location.pathname.match(
    /^\/editor\/([0-9a-f]{12})\/assembly\/([0-9a-f]{12})/,
  );
  return match ? { sid: match[1], aid: match[2] } : { sid: '', aid: '' };
}

/**
 * Convertit un AssemblySection en blocks BlockNote natifs read-only
 * (heading + paragraph + placeholder pour composants).
 *
 * Vague E2 Commit E2 = display brut. Commits F1-F5 vont remplacer les
 * placeholders par les vrais custom blocks.
 */
function sectionToInitialBlocks(sections: AssemblySection[]): any[] {
  const blocks: any[] = [];
  for (const section of sections) {
    // Section title -> heading level 2
    if (section.title) {
      blocks.push({
        type: 'heading',
        props: { level: 2 },
        content: section.title,
      });
    }
    // Narrative markdown -> paragraph (V1 simple, F3 fera markdown -> blocks natifs)
    if (section.narrative_md) {
      blocks.push({
        type: 'paragraph',
        content: section.narrative_md.slice(0, 500), // tronqué V1
      });
    }
    // Components -> placeholder text V1 (F1-F5 ajouteront custom blocks)
    for (const comp of section.components || []) {
      blocks.push({
        type: 'paragraph',
        content: [
          { type: 'text', text: '📎 Composant : ', styles: { bold: true } },
          { type: 'text', text: comp.ref, styles: { code: true } },
          {
            type: 'text',
            text: ' (custom block à venir — Commits F1-F5)',
            styles: { italic: true, textColor: 'gray' },
          },
        ],
      });
    }
  }
  return blocks;
}

function App() {
  const { sid, aid } = parseRouteParams();
  const [assembly, setAssembly] = useState<AssemblyFetchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Fetch l'assembly via API hub
  useEffect(() => {
    if (!sid || !aid) {
      setError("URL invalide. Attendu : /editor/{sid 12hex}/assembly/{aid 12hex}");
      setLoading(false);
      return;
    }
    fetchAssembly(sid, aid)
      .then((data) => {
        setAssembly(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(String(err.message || err));
        setLoading(false);
      });
  }, [sid, aid]);

  // Construire les blocks initiaux depuis l'assembly chargé
  const initialContent =
    assembly?.manifest?.layout?.sections
      ? sectionToInitialBlocks(assembly.manifest.layout.sections)
      : [
          {
            type: 'paragraph',
            content: loading ? 'Chargement…' : (error || 'Aucun contenu'),
          },
        ];

  const editor = useCreateBlockNote({
    initialContent: initialContent.length > 0 ? initialContent : [
      { type: 'paragraph', content: 'Assembly vide.' },
    ],
  });

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Header sobre DSFR-inspired */}
      <header
        style={{
          padding: '12px 24px',
          background: '#fff',
          borderBottom: '1px solid #e5e5e5',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 16,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <strong style={{ color: '#000091', fontSize: 14 }}>
            CEREMA · QGIS · Éditeur
          </strong>
          {assembly?.manifest && (
            <span style={{ color: '#666', fontSize: 13 }}>
              {assembly.manifest.title}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 12, fontSize: 11, color: '#666' }}>
          {assembly?.metadata && (
            <>
              <span>v{assembly.metadata.version_num}</span>
              <span>•</span>
              <span style={{ color: '#0063cb' }}>
                {assembly.manifest.audience}
              </span>
            </>
          )}
        </div>
      </header>

      {/* Erreur si fetch fail */}
      {error && (
        <div
          style={{
            padding: '16px 24px',
            background: '#fee5d9',
            color: '#a50f15',
            borderBottom: '1px solid #fcbba1',
            fontSize: 13,
          }}
        >
          <strong>Erreur :</strong> {error}
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div
          style={{
            padding: '40px',
            textAlign: 'center',
            color: '#666',
            fontSize: 14,
          }}
        >
          Chargement de l'assembly {aid}…
        </div>
      )}

      {/* BlockNote editor (read-only V1, F1-F5 ajouteront custom blocks éditables) */}
      {!loading && !error && (
        <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
          <BlockNoteView editor={editor} theme="light" editable={false} />
        </div>
      )}

      {/* Footer status */}
      <footer
        style={{
          padding: '8px 24px',
          background: '#f6f6f6',
          borderTop: '1px solid #e5e5e5',
          fontSize: 11,
          color: '#666',
          display: 'flex',
          gap: 12,
        }}
      >
        <span>D-QGIS-010 · BlockNote v0.22</span>
        <span>•</span>
        <span>Vague E2 Commit E2 (read-only)</span>
        <span style={{ marginLeft: 'auto' }}>
          {assembly?.manifest?.layout?.sections?.length ?? 0} sections
        </span>
      </footer>
    </div>
  );
}

export default App;
