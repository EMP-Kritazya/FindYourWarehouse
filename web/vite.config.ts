import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Override with PROINSPECT_API=http://localhost:PORT npm run dev to point elsewhere.
const API = process.env.PROINSPECT_API || 'http://localhost:8010'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // keep maplibre un-prebundled so it and its worker file stay side by side (and one module instance)
  optimizeDeps: { exclude: ['maplibre-gl'] },
  server: {
    port: 8080,
    strictPort: true,
    proxy: {
      '/api': { target: API, changeOrigin: true },
      '/media': { target: API, changeOrigin: true },
      '/ws': { target: API, changeOrigin: true, ws: true },
    },
  },
  preview: {
    port: 8080,
    strictPort: true,
    proxy: {
      '/api': { target: API, changeOrigin: true },
      '/media': { target: API, changeOrigin: true },
      '/ws': { target: API, changeOrigin: true, ws: true },
    },
  },
})
