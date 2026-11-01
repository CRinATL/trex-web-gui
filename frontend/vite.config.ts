/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    // must equal DEV_PORT in backend/trex_web_gui/cli.py: the backend accepts this browser origin only with --dev
    port: 5173,
    strictPort: true,
    // no changeOrigin: Host stays 127.0.0.1:<vite port>, which the backend's TrustedHostMiddleware allows
    proxy: { '/api': { target: 'http://127.0.0.1:8765', ws: true } },
  },
  // Served from the loopback backend, so chunk size costs nothing measurable; pages are mounted hidden by design
  // (Control, Studio), which a lazy split would complicate (ruling Task 7 M1). The default advisory is 500 kB.
  build: { chunkSizeWarningLimit: 1000 },
  // vmThreads keeps per-file isolation; setting the pool explicitly keeps vitest's environment hint off.
  test: { environment: 'jsdom', setupFiles: ['./src/setupTests.ts'], globals: false, pool: 'vmThreads' },
})
