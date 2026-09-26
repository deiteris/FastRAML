import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Fixed names in the extension's `media/`, for `page()` in `../src/extension.ts`.
export default defineConfig({
  plugins: [react()],
  base: './',
  publicDir: false,
  build: {
    outDir: '../media',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1024,
    rollupOptions: {
      input: 'src/main.tsx',
      output: { entryFileNames: 'viewer.js', assetFileNames: 'viewer[extname]' },
    },
  },
});
