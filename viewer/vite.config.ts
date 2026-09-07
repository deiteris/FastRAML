import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// `base: './'` so a built bundle opens from a file path or any sub-directory:
// the output is meant to be handed to someone, not only served from a root.
export default defineConfig({ plugins: [react()], base: './', build: { outDir: 'dist' } });
