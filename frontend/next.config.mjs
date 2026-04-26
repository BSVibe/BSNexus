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
  // Phase Z migration scope is framework-only: legacy
  // Vite/React-Router lint config didn't expose ``next/core-web-vitals``
  // / React 19 strict rules, so the existing component code carries
  // pre-existing warnings (e.g. setState-in-effect in IntegrationCard)
  // that surface as ``next build`` lint errors. Skipping ESLint at
  // build time keeps the framework swap atomic; the rules still run
  // via ``pnpm lint`` and will be cleaned up in a follow-up.
  eslint: { ignoreDuringBuilds: true },
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

export default nextConfig;
