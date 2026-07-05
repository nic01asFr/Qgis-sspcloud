/**
 * Vitest config editeur BlockNote.
 *
 * Sprint V1.18 Vague 1 Equipe C (2026-07-05).
 */
import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config';

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      globals: false,
      include: ['src/**/*.test.{ts,tsx}'],
      exclude: ['node_modules', 'dist'],
    },
  }),
);
