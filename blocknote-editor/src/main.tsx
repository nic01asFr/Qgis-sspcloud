/**
 * Editeur BlockNote standalone qgis-sspcloud - Entry React.
 *
 * D-QGIS-010 Vague E2 pivot UI. Bundle Vite output dans
 * hub/hub/static/blocknote-editor/, servi par endpoint FastAPI
 * GET /editor/{sid}/assembly/{aid}.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import { Toaster } from 'sonner';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import './editor-layout.css';

// Sprint 3 P2 (8.14) : monitoring client errors
// Capture les erreurs JavaScript non-gerees + promesses rejetees + envoie
// au hub via POST /api/log/client-error (ring buffer 100 RAM cote serveur).
function postClientError(payload: Record<string, any>) {
  // Best-effort, ne bloque pas l'app si le POST echoue
  try {
    fetch('/api/log/client-error', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...payload,
        ua: navigator.userAgent,
        url: window.location.pathname,
      }),
      keepalive: true,
    }).catch(() => {/* swallow : ne pas declencher un boucle d'erreur */});
  } catch {/* idem */}
}

window.addEventListener('error', (event) => {
  postClientError({
    message: event.message || 'Error event',
    stack: event.error?.stack,
    line: event.lineno,
    context: { type: 'error', filename: event.filename },
  });
});

window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason;
  postClientError({
    message:
      reason instanceof Error
        ? reason.message
        : typeof reason === 'string'
        ? reason
        : 'Unhandled promise rejection',
    stack: reason instanceof Error ? reason.stack : undefined,
    context: { type: 'unhandledrejection' },
  });
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
      {/* Sprint V1.18 R3 : sonner Toaster global (top-right, DSFR-like). */}
      <Toaster
        position="top-right"
        richColors
        closeButton
        toastOptions={{
          style: {
            fontFamily: 'Marianne, system-ui, sans-serif',
            fontSize: 13,
          },
        }}
      />
    </ErrorBoundary>
  </React.StrictMode>,
);
