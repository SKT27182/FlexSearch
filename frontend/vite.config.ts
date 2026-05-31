import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

function resolveAllowedHosts(env: Record<string, string>): string[] {
  const hosts = new Set(['localhost', '127.0.0.1'])
  const publicHost = env.VITE_APP_PUBLIC_HOST?.trim()
  if (publicHost) hosts.add(publicHost)
  for (const part of (env.VITE_ALLOWED_HOSTS ?? '').split(',')) {
    const host = part.trim()
    if (host) hosts.add(host)
  }
  return [...hosts]
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const vitePort = Number(env.VITE_PORT || '5144')
  const apiTarget = env.VITE_DEV_API_TARGET || 'http://localhost:8889'
  const allowedHosts = resolveAllowedHosts(env)

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      host: '0.0.0.0',
      port: vitePort,
      allowedHosts,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyRes', (proxyRes, req, res) => {
              const path = req.url ?? '';
              if (!path.includes('/documents/events')) return;
              res.setHeader('Cache-Control', 'no-cache');
              res.setHeader('X-Accel-Buffering', 'no');
              delete proxyRes.headers['content-encoding'];
            });
          },
        },
      },
    },
  }
})
