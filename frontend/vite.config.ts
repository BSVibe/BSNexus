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
        : true,
      host: '0.0.0.0',
      port: 3000,
      proxy: {
        '/api': {
          target: env.VITE_API_URL || 'http://localhost:8000',
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
