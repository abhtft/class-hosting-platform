import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run dev` (from the project root) starts this site on :3000 and the local backend on :8788. Vite forwards /api to the backend,
// so the browser sees ONE address, exactly like on the platform: always call a RELATIVE /api/... (no CORS, nothing to configure).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist', // the platform publishes dist/ (build/ and out/ also work)
    emptyOutDir: true,
  },
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://127.0.0.1:8788',
    },
  },
})
