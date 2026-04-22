import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', 'VITE_')

  // ``E2E_PROXY_TARGET`` is server-side only — used by the isolated
  // e2e orchestrator to point the dev proxy at an ephemeral backend
  // without leaking the URL into the client bundle. Production /
  // dev devcontainer paths still go through ``VITE_API_URL`` from the
  // .env file (or empty, which gives relative ``/api`` paths).
  const apiUrl =
    process.env.E2E_PROXY_TARGET || env.VITE_API_URL || 'http://localhost:8000'
  const allowedHosts = process.env.VITE_ALLOWED_HOSTS || env.VITE_ALLOWED_HOSTS

  return {
    envDir: '..',
    plugins: [react()],
    server: {
      allowedHosts: allowedHosts
        ? allowedHosts.split(',').map((h) => h.trim())
        : true,
      host: '0.0.0.0',
      port: 3000,
      proxy: {
        '/api': {
          target: apiUrl,
          changeOrigin: false,
          headers: { 'X-Forwarded-Host': '' },  // placeholder, overridden per-request
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq, req) => {
              if (req.headers.host) {
                proxyReq.setHeader('X-Forwarded-Host', req.headers.host)
              }
            })
          },
        },
      },
    },
  }
})
