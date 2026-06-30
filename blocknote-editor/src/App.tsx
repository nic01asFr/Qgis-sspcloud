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
import { useCallback, useEffect, useRef, useState } from 'react';
import { useCreateBlockNote } from '@blocknote/react';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/mantine/style.css';
import { fetchAssembly } from './api';
import { qgisBlockNoteSchema } from './blocks';
import { assemblyToBlockNoteDoc } from './serializer';
import { useAutosave, saveBlocks, type SaveStatus } from './autosave';
import { EditPanel, type EditableBlock } from './EditPanel';
import { updateAssembly } from './api';
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

  // Audit v1.7.2 P2 : restreindre deps a sid+aid+version_num (eviter
  // re-fetch des composants a chaque save qui change l'objet `assembly`).
  const aid = assembly.metadata?.aid;
  const vnum = assembly.metadata?.version_num;
  useEffect(() => {
    assemblyToBlockNoteDoc(sid, assembly.manifest)
      .then(setBlocks)
      .catch((err) => setSerializeError(String(err.message || err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, aid, vnum]);

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
    <>
      {/* v1.11 Phase D : Titre Assembly = textbox dedie en haut (pattern Docs).
          Editable inline avec autosave PUT update_assembly debounce 30s. */}
      <AssemblyTitle
        sid={sid}
        aid={assembly.metadata?.aid || ''}
        manifest={assembly.manifest}
        versionNumSource={assembly.metadata?.version_num || 1}
        onVersionUpdate={onVersionUpdate}
      />
      <BlockNoteContent
        key={`bn-${assembly.metadata?.version_num ?? 0}`}
        sid={sid}
        aid={assembly.metadata?.aid || ''}
        assembly={assembly}
        initialBlocks={blocks}
        onVersionUpdate={onVersionUpdate}
      />
    </>
  );
}

/**
 * v1.11 Phase D : titre Assembly editable inline, pattern Docs.
 * Champ separe du contenu (vs customHeading H1 1er block) — saut semantique
 * "titre du livrable" vs "contenu blocks".
 *
 * Autosave debounce 30s + PUT update_assembly avec version_num_source OCC.
 */
function AssemblyTitle({
  sid,
  aid,
  manifest,
  versionNumSource,
  onVersionUpdate,
}: {
  sid: string;
  aid: string;
  manifest: any;
  versionNumSource: number;
  onVersionUpdate: (n: number) => void;
}) {
  const [title, setTitle] = useState<string>(String(manifest.title || ''));
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const timerRef = useRef<number | null>(null);
  const lastSavedRef = useRef<string>(String(manifest.title || ''));

  useEffect(() => {
    // Reset au changement de manifest (reload)
    setTitle(String(manifest.title || ''));
    lastSavedRef.current = String(manifest.title || '');
  }, [manifest.title]);

  useEffect(() => {
    if (title === lastSavedRef.current) return;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(async () => {
      setStatus('saving');
      try {
        const newManifest = { ...manifest, title };
        const result = await updateAssembly(sid, aid, newManifest, versionNumSource);
        if (result.ok && result.newVersionNum) {
          lastSavedRef.current = title;
          setStatus('saved');
          onVersionUpdate(result.newVersionNum);
          setTimeout(() => setStatus('idle'), 2000);
        } else {
          setStatus('error');
        }
      } catch {
        setStatus('error');
      }
    }, 30_000);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title]);

  return (
    <div
      style={{
        padding: '20px 24px 8px',
        background: '#fff',
        borderBottom: '1px solid #f0f0f0',
        position: 'relative',
      }}
    >
      <input
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Titre du livrable…"
        aria-label="Titre du livrable"
        style={{
          width: '100%',
          fontSize: 30,
          fontWeight: 700,
          color: '#000091',
          background: 'transparent',
          border: 'none',
          outline: 'none',
          padding: 0,
          fontFamily: 'Marianne, system-ui, sans-serif',
          fontStyle: title ? 'normal' : 'italic',
        }}
      />
      <div
        style={{
          fontSize: 11,
          color: status === 'error' ? '#a50f15' : status === 'saving' ? '#0063cb' : status === 'saved' ? '#1f8d4d' : '#999',
          marginTop: 4,
          height: 14,
          transition: 'color 0.2s',
        }}
      >
        {status === 'idle' && title !== lastSavedRef.current && '• modification en attente'}
        {status === 'saving' && 'Sauvegarde…'}
        {status === 'saved' && '✓ Titre sauvegardé'}
        {status === 'error' && '⚠ Erreur sauvegarde titre'}
      </div>
    </div>
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
  const [overrideStatus, setOverrideStatus] = useState<SaveStatus | null>(null);
  // Sprint 4 v1.10.0 (8.18) : drawer Edit Panel
  const [editingBlock, setEditingBlock] = useState<EditableBlock | null>(null);

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

  const autosaveStatus = useAutosave(
    true,
    sid,
    aid,
    currentBlocks,
    assembly.manifest,
    versionNumSource,
    handleVersionUpdate,
  );

  // Audit v1.7.2 P1 #2 : 'Forcer ecrasement' appelle saveBlocks directement
  // avec versionNumSource = null (bypass check concurrency cote hub).
  const handleForceOverwrite = useCallback(async () => {
    setOverrideStatus({ type: 'saving' });
    try {
      const result = await saveBlocks(
        sid, aid, currentBlocks, assembly.manifest,
        null as any, // null bypass version_num_source côté hub
        [],
      );
      if (result.ok && result.newVersionNum) {
        setOverrideStatus({
          type: 'saved',
          atTime: Date.now(),
          versionNum: result.newVersionNum,
        });
        handleVersionUpdate(result.newVersionNum);
        // Reset override apres 5s pour reprendre l'autosave normal
        setTimeout(() => setOverrideStatus(null), 5000);
      } else {
        setOverrideStatus({
          type: 'error',
          message: result.error || 'Echec ecrasement',
        });
      }
    } catch (err) {
      setOverrideStatus({ type: 'error', message: String(err) });
    }
  }, [sid, aid, currentBlocks, assembly, handleVersionUpdate]);

  // Le status affiche : override (force-save en cours/fini) si présent,
  // sinon le status autosave normal
  const displayStatus = overrideStatus || autosaveStatus;

  // Sprint 4 v1.10.0 (8.18) : exposer un global pour que les custom blocks
  // puissent ouvrir le drawer EditPanel via window.__openEditPanel(block).
  // BlockNote createReactBlockSpec ne permet pas de passer un callback en
  // prop direct -> bridge global pour eviter prop drilling complexe.
  useEffect(() => {
    (window as any).__openEditPanel = (block: EditableBlock) => {
      setEditingBlock(block);
    };
    return () => {
      delete (window as any).__openEditPanel;
    };
  }, []);

  // Apres save Edit Panel : met a jour le block dans BlockNote editor + reset
  const handleEditPanelSaved = useCallback(
    (newProps: Record<string, any>) => {
      if (!editingBlock) return;
      // BlockNote API : trouver le block et update via editor.updateBlock
      try {
        const blockInDoc = editor.document.find((b: any) => b.id === editingBlock.id);
        if (blockInDoc) {
          editor.updateBlock(blockInDoc, { props: newProps });
          // Trigger autosave bypass : sync currentBlocks
          setCurrentBlocks(editor.document);
        }
      } catch (err) {
        console.warn('updateBlock failed', err);
      }
    },
    [editingBlock, editor],
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
      <EditPanel
        block={editingBlock}
        sid={sid}
        versionNumSource={versionNumSource}
        onClose={() => setEditingBlock(null)}
        onSaved={handleEditPanelSaved}
      />
      <SaveStatusBar
        status={displayStatus}
        onForceOverwrite={handleForceOverwrite}
        onReload={() => window.location.reload()}
      />
    </>
  );
}

/**
 * Barre d'état autosave (Notion-style "Sauvegardé il y a Xs").
 *
 * Audit v1.7.2 P1 #2 : sur status='conflict', expose 2 boutons :
 * - 'Recharger' : revient à la version actuelle du serveur (perd les modifs locales)
 * - 'Forcer écrasement' : envoie le PUT sans version_num_source (écrase serveur)
 *
 * Audit v1.7.2 P2 : setInterval 1Hz mis en pause si status idle/saved-vieux/error
 * (économise 60 ticks/min en idle).
 */
function SaveStatusBar({
  status,
  onForceOverwrite,
  onReload,
}: {
  status: SaveStatus;
  onForceOverwrite: () => void;
  onReload: () => void;
}) {
  const [now, setNow] = useState(Date.now());

  // Le timer ne tourne QUE si on a besoin d'afficher un compteur live
  const needsTicker =
    status.type === 'pending' ||
    (status.type === 'saved' && Date.now() - status.atTime < 60_000);

  useEffect(() => {
    if (!needsTicker) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [needsTicker]);

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
    label = `⚠ Conflit : l'assembly a été modifié ailleurs (serveur v${status.currentVersionNum}, vous v${status.sourceVersionNum})`;
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
        display: 'flex',
        alignItems: 'center',
        gap: 12,
      }}
    >
      <span style={{ flex: 1 }}>{label}</span>
      {status.type === 'conflict' && (
        <>
          <button
            type="button"
            onClick={onReload}
            style={{
              padding: '4px 10px',
              fontSize: 11,
              border: '1px solid #0063cb',
              background: '#fff',
              color: '#0063cb',
              borderRadius: 3,
              cursor: 'pointer',
              fontWeight: 500,
            }}
          >
            Recharger (perdre mes modifs)
          </button>
          <button
            type="button"
            onClick={onForceOverwrite}
            style={{
              padding: '4px 10px',
              fontSize: 11,
              border: '1px solid #a50f15',
              background: '#a50f15',
              color: '#fff',
              borderRadius: 3,
              cursor: 'pointer',
              fontWeight: 500,
            }}
          >
            Forcer écrasement
          </button>
        </>
      )}
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
              {/* Sprint 2 P1 (8.8) : bouton 'Apercu DSFR strict' qui ouvre
                  la storymap publiable dans un drawer modal. Marie peut voir
                  le rendu final (avec header/footer DSFR, mentions legales)
                  vs vue 'nue' BlockNote. */}
              <span>•</span>
              <button
                type="button"
                onClick={() => {
                  const url = `/studies/${sid}/assemblies/${aid}/render`;
                  // Ouvrir dans un drawer modal full-height
                  const drawer = document.getElementById('dsfr-preview-drawer');
                  if (drawer) drawer.remove();
                  const overlay = document.createElement('div');
                  overlay.id = 'dsfr-preview-drawer';
                  overlay.style.cssText = `
                    position: fixed; inset: 0; z-index: 9999;
                    background: rgba(0,0,0,0.5); display: flex;
                    align-items: stretch; justify-content: flex-end;
                  `;
                  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
                  const drawer2 = document.createElement('div');
                  drawer2.style.cssText = `
                    width: 75%; max-width: 1400px; background: #fff;
                    display: flex; flex-direction: column;
                    box-shadow: -8px 0 24px rgba(0,0,0,0.15);
                  `;
                  drawer2.innerHTML = `
                    <div style="padding:12px 20px;background:#000091;color:#fff;
                                display:flex;justify-content:space-between;
                                align-items:center;font-family:system-ui">
                      <strong style="font-size:14px">Aperçu DSFR strict — storymap publiable</strong>
                      <button id="dsfr-drawer-close" style="background:none;border:none;
                              color:#fff;font-size:24px;cursor:pointer;padding:0 8px">×</button>
                    </div>
                    <iframe src="${url}" style="flex:1;width:100%;border:none"
                            title="Aperçu DSFR strict"></iframe>
                  `;
                  overlay.appendChild(drawer2);
                  document.body.appendChild(overlay);
                  document.getElementById('dsfr-drawer-close')?.addEventListener(
                    'click', () => overlay.remove(),
                  );
                }}
                style={{
                  background: '#fff',
                  border: '1px solid #000091',
                  color: '#000091',
                  padding: '3px 10px',
                  fontSize: 11,
                  borderRadius: 3,
                  cursor: 'pointer',
                  fontWeight: 500,
                }}
                title="Voir le rendu DSFR final (avec header/footer/mentions legales)"
              >
                👁 Aperçu DSFR
              </button>
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
        <span>{`v${__EDITOR_VERSION__} (13 blocks · autosave 30s · conflict resolver)`}</span>
        <span style={{ marginLeft: 'auto' }}>
          {assembly?.manifest?.layout?.sections?.length ?? 0} sections
        </span>
      </footer>
    </div>
  );
}

export default App;
