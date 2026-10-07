import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    // En dev, /api est redirigé vers le pont API local (server/api.py).
    proxy: { '/api': 'http://127.0.0.1:8787' },
  },
})
