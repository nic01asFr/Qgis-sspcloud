/**
 * Editeur BlockNote standalone qgis-sspcloud - Entry React.
 *
 * D-QGIS-010 Vague E2 pivot UI. Bundle Vite output dans
 * hub/hub/static/blocknote-editor/, servi par endpoint FastAPI
 * GET /editor/{sid}/assembly/{aid}.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
