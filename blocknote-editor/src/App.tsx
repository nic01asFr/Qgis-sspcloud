/**
 * Editeur BlockNote - App principal.
 *
 * Vague E2 Commit H2 (D-QGIS-010) : editable=true + autosave 30s +
 * indicateur "Sauvegardé".
 *
 * Architecture :
 * - App : fetch assembly + states loading/error
 * - EditorContent : serialise assembly -> blocks, recoit setBlocks de
 *   BlockNoteContent pour autosave
 * - BlockNoteContent : useCreateBlockNote + onChange handler + autosave hook
 */
import { useCallback, useEffect, useState } from 'react';
import { useCreateBlockNote } from '@blocknote/react';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/mantine/style.css';
import { fetchAssembly } from './api';
import { qgisBlockNoteSchema } from './blocks';
import { assemblyToBlockNoteDoc } from './serializer';
import { useAutosave, type SaveStatus } from './autosave';
import type { AssemblyFetchResponse } from './types';

function parseRouteParams(): { sid: string; aid: string } {
  const match = window.location.pathname.match(
    /^\/editor\/([0-9a-f]{12})\/assembly\/([0-9a-f]{12})/,
  );
  return match ? { sid: match[1], aid: match[2] } : { sid: '', aid: '' };
}

/**
 * Sub-component qui rend BlockNote APRES le fetch assembly.
 * Sépare le rendering pour que useCreateBlockNote ne soit jamais appelé
 * avec content vide.
 */
function EditorContent({
  sid,
  assembly,
  onVersionUpdate,
}: {
  sid: string;
  assembly: AssemblyFetchResponse;
  onVersionUpdate: (newVersionNum: number) => void;
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

  // Audit fix v1.7.1 P0 #2 : key={version_num} force remount BlockNoteContent
  // quand l'assembly est rafraîchi (sinon useCreateBlockNote garde le
  // contenu initial mounté, désynchro vs currentBlocks state).
  return (
    <BlockNoteContent
      key={`bn-${assembly.metadata?.version_num ?? 0}`}
      sid={sid}
      aid={assembly.metadata?.aid || ''}
      assembly={assembly}
      initialBlocks={blocks}
      onVersionUpdate={onVersionUpdate}
    />
  );
}

/**
 * Wrapper final qui initialise BlockNote + autosave hook.
 */
function BlockNoteContent({
  sid,
  aid,
  assembly,
  initialBlocks,
  onVersionUpdate,
}: {
  sid: string;
  aid: string;
  assembly: AssemblyFetchResponse;
  initialBlocks: any[];
  onVersionUpdate: (newVersionNum: number) => void;
}) {
  const [currentBlocks, setCurrentBlocks] = useState<any[]>(initialBlocks);
  const [versionNumSource, setVersionNumSource] = useState<number>(
    assembly.metadata?.version_num || 1,
  );

  const editor = useCreateBlockNote({
    schema: qgisBlockNoteSchema,
    initialContent: initialBlocks.length > 0 ? initialBlocks : [
      { type: 'paragraph', content: 'Assembly vide.' },
    ],
  });

  const handleVersionUpdate = useCallback(
    (newVersionNum: number) => {
      setVersionNumSource(newVersionNum);
      onVersionUpdate(newVersionNum);
    },
    [onVersionUpdate],
  );

  const status = useAutosave(
    true, // enabled
    sid,
    aid,
    currentBlocks,
    assembly.manifest,
    versionNumSource,
    handleVersionUpdate,
  );

  return (
    <>
      <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
        <BlockNoteView
          editor={editor}
          theme="light"
          editable={true}
          onChange={() => setCurrentBlocks(editor.document)}
        />
      </div>
      <SaveStatusBar status={status} />
    </>
  );
}

/**
 * Barre d'état autosave (Notion-style "Sauvegardé il y a Xs").
 */
function SaveStatusBar({ status }: { status: SaveStatus }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  let label = '';
  let color = '#666';
  if (status.type === 'idle') {
    label = 'En attente de modifications…';
  } else if (status.type === 'pending') {
    const elapsed = Math.round((now - status.sinceMs) / 1000);
    const remaining = Math.max(0, 30 - elapsed);
    label = `Modifications en attente — sauvegarde dans ${remaining}s`;
    color = '#b34000';
  } else if (status.type === 'saving') {
    label = 'Sauvegarde en cours…';
    color = '#0063cb';
  } else if (status.type === 'saved') {
    const elapsed = Math.round((now - status.atTime) / 1000);
    label =
      elapsed < 5
        ? `✓ Sauvegardé (v${status.versionNum})`
        : `✓ Sauvegardé il y a ${elapsed}s (v${status.versionNum})`;
    color = '#1f8d4d';
  } else if (status.type === 'error') {
    label = `⚠ Erreur sauvegarde : ${status.message.slice(0, 80)}`;
    color = '#a50f15';
  } else if (status.type === 'conflict') {
    label = `⚠ Conflit (v${status.currentVersionNum} vs v${status.sourceVersionNum}) — rechargez la page`;
    color = '#a50f15';
  }

  return (
    <div
      style={{
        padding: '6px 24px',
        background: '#fff',
        borderTop: '1px solid #e5e5e5',
        fontSize: 12,
        color,
        fontWeight: 500,
      }}
    >
      {label}
    </div>
  );
}

function App() {
  const { sid, aid } = parseRouteParams();
  const [assembly, setAssembly] = useState<AssemblyFetchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentVersionNum, setCurrentVersionNum] = useState<number>(1);

  useEffect(() => {
    if (!sid || !aid) {
      setError("URL invalide. Attendu : /editor/{sid 12hex}/assembly/{aid 12hex}");
      setLoading(false);
      return;
    }
    fetchAssembly(sid, aid)
      .then((data) => {
        setAssembly(data);
        setCurrentVersionNum(data.metadata?.version_num || 1);
        setLoading(false);
      })
      .catch((err) => {
        setError(String(err.message || err));
        setLoading(false);
      });
  }, [sid, aid]);

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
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
            <span style={{ color: '#666', fontSize: 13 }}>{assembly.manifest.title}</span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 12, fontSize: 11, color: '#666', alignItems: 'center' }}>
          {assembly?.metadata && (
            <>
              <span>v{currentVersionNum}</span>
              <span>•</span>
              <span style={{ color: '#0063cb' }}>{assembly.manifest.audience}</span>
            </>
          )}
        </div>
      </header>

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

      {loading && (
        <div style={{ padding: 40, textAlign: 'center', color: '#666', fontSize: 14 }}>
          Chargement de l'assembly {aid}…
        </div>
      )}

      {!loading && !error && assembly && (
        <EditorContent
          sid={sid}
          assembly={assembly}
          onVersionUpdate={setCurrentVersionNum}
        />
      )}

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
        <span>Vague E2 H2 (13 blocks + autosave 30s)</span>
        <span style={{ marginLeft: 'auto' }}>
          {assembly?.manifest?.layout?.sections?.length ?? 0} sections
        </span>
      </footer>
    </div>
  );
}

export default App;
