import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', 'VITE_')

  return {
    envDir: '..',
    plugins: [react()],
    server: {
      allowedHosts: env.VITE_ALLOWED_HOSTS
        ? env.VITE_ALLOWED_HOSTS.split(',').map((h) => h.trim())
        : [],
      host: '0.0.0.0',
      port: 3000,
      proxy: {
        '/api': 'http://localhost:8000',
      },
    },
  }
})
