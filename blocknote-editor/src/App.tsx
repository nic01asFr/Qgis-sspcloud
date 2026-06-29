/**
 * Editeur BlockNote - App principal.
 *
 * Vague E2 Commits F1+F2+F3 (D-QGIS-010) : 6 custom blocks DOM + EditorContent
 * pattern (sub-component qui s'initialise APRES le fetch assembly).
 *
 * Architecture corrigée vs E2 :
 * - App fait le fetch (loading/error states)
 * - EditorContent reçoit l'assembly chargé et initialise BlockNote avec
 *   les blocks dérivés via assemblyToBlockNoteDoc()
 * - useCreateBlockNote() est appelé UNE FOIS avec le bon contenu initial
 *   (vs E2 où il était appelé avant le fetch -> stuck "Chargement…")
 */
import { useEffect, useState } from 'react';
import { useCreateBlockNote } from '@blocknote/react';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/mantine/style.css';
import { fetchAssembly } from './api';
import { qgisBlockNoteSchema } from './blocks';
import { assemblyToBlockNoteDoc } from './serializer';
import type { AssemblyFetchResponse } from './types';

function parseRouteParams(): { sid: string; aid: string } {
  const match = window.location.pathname.match(
    /^\/editor\/([0-9a-f]{12})\/assembly\/([0-9a-f]{12})/,
  );
  return match ? { sid: match[1], aid: match[2] } : { sid: '', aid: '' };
}

/**
 * Sub-component qui rend BlockNote APRES le fetch assembly.
 * useCreateBlockNote initialisé avec le bon contenu = pas de stuck "Loading".
 */
function EditorContent({
  sid,
  assembly,
}: {
  sid: string;
  assembly: AssemblyFetchResponse;
}) {
  const [blocks, setBlocks] = useState<any[] | null>(null);
  const [serializeError, setSerializeError] = useState<string | null>(null);

  useEffect(() => {
    assemblyToBlockNoteDoc(sid, assembly.manifest)
      .then(setBlocks)
      .catch((err) => setSerializeError(String(err.message || err)));
  }, [sid, assembly]);

  if (serializeError) {
    return (
      <div style={{ padding: 20, color: '#a50f15' }}>
        Erreur sérialisation : {serializeError}
      </div>
    );
  }

  if (blocks === null) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: '#666' }}>
        Préparation des blocks…
      </div>
    );
  }

  return <BlockNoteContent initialBlocks={blocks} />;
}

/**
 * Wrapper final qui initialise useCreateBlockNote avec les blocks préparés.
 * Séparé pour que useCreateBlockNote ne soit JAMAIS appelé avec content vide.
 */
function BlockNoteContent({ initialBlocks }: { initialBlocks: any[] }) {
  const editor = useCreateBlockNote({
    schema: qgisBlockNoteSchema,
    initialContent: initialBlocks.length > 0 ? initialBlocks : [
      { type: 'paragraph', content: 'Assembly vide.' },
    ],
  });

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
      <BlockNoteView editor={editor} theme="light" editable={false} />
    </div>
  );
}

function App() {
  const { sid, aid } = parseRouteParams();
  const [assembly, setAssembly] = useState<AssemblyFetchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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
        <div style={{ display: 'flex', gap: 12, fontSize: 11, color: '#666', alignItems: 'center' }}>
          {assembly?.metadata && (
            <>
              <span>v{assembly.metadata.version_num}</span>
              <span>•</span>
              <span style={{ color: '#0063cb' }}>{assembly.manifest.audience}</span>
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
        <div style={{ padding: 40, textAlign: 'center', color: '#666', fontSize: 14 }}>
          Chargement de l'assembly {aid}…
        </div>
      )}

      {/* Editor (rendu APRES le fetch via EditorContent) */}
      {!loading && !error && assembly && (
        <EditorContent sid={sid} assembly={assembly} />
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
        <span>Vague E2 F-DOM (6 custom blocks)</span>
        <span style={{ marginLeft: 'auto' }}>
          {assembly?.manifest?.layout?.sections?.length ?? 0} sections
        </span>
      </footer>
    </div>
  );
}

export default App;
