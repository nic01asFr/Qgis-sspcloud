import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

/**
 * Vite config pour l'editeur BlockNote standalone qgis-sspcloud.
 *
 * Output par defaut : ./dist (relatif au dossier blocknote-editor/).
 * En build Docker multi-stage, le Dockerfile.hub fait :
 *   COPY --from=blocknote-builder /build/dist -> hub/static/blocknote-editor/
 * En build local hors Docker, il faut copier manuellement dist/ vers
 *   hub/hub/static/blocknote-editor/ ou utiliser un script post-build.
 *
 * Cf. ADR D-QGIS-010 + docs/blocknote-editor-plan.md.
 */
export default defineConfig({
  plugins: [react()],
  // Base URL : les assets sont servis depuis /static/blocknote-editor/
  base: '/static/blocknote-editor/',
  build: {
    // Output dans ./dist (relatif au dossier blocknote-editor/).
    // En Docker multi-stage : /build/dist (le builder copy ensuite).
    // En local : COPY manuel vers ../hub/hub/static/blocknote-editor/.
    outDir: 'dist',
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
