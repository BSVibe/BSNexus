import createNextIntlPlugin from 'next-intl/plugin';

// Wires the next-intl request config (`src/i18n/request.ts`) into the
// build so server components / `getMessages()` resolve message bundles.
const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

/** @type {import('next').NextConfig} */

// API base for the dev rewrite proxy. ``E2E_PROXY_TARGET`` is server-side
// only and used by the isolated e2e orchestrator to point at an ephemeral
// backend; production paths use ``NEXT_PUBLIC_API_URL`` from the client
// directly (or relative ``/api`` if same-origin).
const apiUrl =
  process.env.E2E_PROXY_TARGET ||
  process.env.NEXT_PUBLIC_API_URL ||
  process.env.VITE_API_URL ||
  'http://localhost:18100';

const nextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: ['bsserver'],
  devIndicators: false,
  // Migrated from vite.config.ts ``server.proxy.'/api'`` — Next.js
  // rewrites give the same dev-server proxy semantics so cookies stay
  // first-party and the SPA can hit relative ``/api`` paths.
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${apiUrl}/api/:path*`,
      },
    ];
  },
};

export default withNextIntl(nextConfig);
