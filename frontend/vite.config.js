import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'

// MapLibre resolves its worker as <main bundle dir>/maplibre-gl-worker.mjs.
// Vite doesn't copy that node_modules file into the build output, so emit it
// alongside the bundle.
function maplibreWorker() {
  return {
    name: 'maplibre-worker',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'assets/maplibre-gl-worker.mjs',
        source: readFileSync(new URL(
          './node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs',
          import.meta.url)),
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), maplibreWorker()],
  // MapLibre creates its worker via import.meta.url; prebundling rewrites that
  // URL and the worker fails to load ("Worker failed to load") in dev.
  optimizeDeps: { exclude: ['maplibre-gl'] },
  worker: { format: 'es' },
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8000' },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.js'],
  },
})
