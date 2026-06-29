import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

/**
 * Vite config pour l'editeur BlockNote standalone qgis-sspcloud.
 *
 * Output : hub/static/blocknote-editor/ (bundle servi statiquement par hub
 * FastAPI). Cf. ADR D-QGIS-010.
 *
 * Endpoint hub : GET /editor/{sid}/assembly/{aid} retourne index.html
 * (qui charge ce bundle).
 */
export default defineConfig({
  plugins: [react()],
  // Base URL : les assets sont servis depuis /static/blocknote-editor/
  base: '/static/blocknote-editor/',
  build: {
    // Output direct dans le dossier static du hub Python
    outDir: resolve(__dirname, '../hub/hub/static/blocknote-editor'),
    emptyOutDir: true,
    sourcemap: true,
    // Hash assets pour cache-busting automatique
    rollupOptions: {
      output: {
        assetFileNames: 'assets/[name]-[hash][extname]',
        chunkFileNames: 'assets/[name]-[hash].js',
        entryFileNames: 'assets/[name]-[hash].js',
      },
    },
  },
  server: {
    // Dev server : proxy vers hub local pour fetch /studies/...
    port: 5173,
    proxy: {
      '/studies': 'http://localhost:8000',
      '/api': 'http://localhost:8000',
    },
  },
});
