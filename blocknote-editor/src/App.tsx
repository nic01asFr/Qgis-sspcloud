/**
 * Editeur BlockNote - App principal.
 *
 * Vague E2 Commit E1 (D-QGIS-010) : version minimale "hello world".
 * Charge BlockNote vide et affiche les params sid/aid depuis URL.
 *
 * Prochains commits :
 * - E2 : fetch assembly + display read-only
 * - F1-F5 : 13 custom blocks Vague E2
 * - G : serialisation bi-dir Assembly <-> BlockNote JSON
 * - H1-H3 : autosave + concurrency + integration desk + tag v1.7.0
 */
import { useCreateBlockNote } from '@blocknote/react';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/mantine/style.css';

/**
 * Parse sid/aid depuis l'URL path /editor/{sid}/assembly/{aid}.
 * Si pas matched (dev local) : valeurs vides.
 */
function parseRouteParams(): { sid: string; aid: string } {
  const path = window.location.pathname;
  const match = path.match(/^\/editor\/([0-9a-f]{12})\/assembly\/([0-9a-f]{12})/);
  if (match) {
    return { sid: match[1], aid: match[2] };
  }
  return { sid: '', aid: '' };
}

function App() {
  const { sid, aid } = parseRouteParams();
  const editor = useCreateBlockNote({
    initialContent: [
      {
        type: 'heading',
        props: { level: 1 },
        content: 'Éditeur BlockNote qgis-sspcloud',
      },
      {
        type: 'paragraph',
        content: [
          'Vague E2 Commit E1 (D-QGIS-010) — version minimale "hello world".',
        ],
      },
      {
        type: 'paragraph',
        content: [
          { type: 'text', text: 'sid : ', styles: { bold: true } },
          { type: 'text', text: sid || '(non défini)', styles: { code: true } },
        ],
      },
      {
        type: 'paragraph',
        content: [
          { type: 'text', text: 'aid : ', styles: { bold: true } },
          { type: 'text', text: aid || '(non défini)', styles: { code: true } },
        ],
      },
      {
        type: 'paragraph',
        content: [
          'Prochain commit (E2) : fetch assembly via API et affichage des sections.',
        ],
      },
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
          gap: 16,
        }}
      >
        <strong style={{ color: '#000091', fontSize: 14 }}>
          CEREMA · QGIS · Éditeur BlockNote
        </strong>
        <span style={{ color: '#666', fontSize: 12 }}>
          {sid && aid ? `Assembly ${aid}` : 'Mode développement'}
        </span>
      </header>

      {/* BlockNote editor */}
      <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
        <BlockNoteView editor={editor} theme="light" />
      </div>

      {/* Footer status */}
      <footer
        style={{
          padding: '8px 24px',
          background: '#f6f6f6',
          borderTop: '1px solid #e5e5e5',
          fontSize: 11,
          color: '#666',
        }}
      >
        D-QGIS-010 · BlockNote v0.22 · Vague E2 pivot UI · Version E1 "hello world"
      </footer>
    </div>
  );
}

export default App;
