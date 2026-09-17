// @ts-check

/**
 * NADDP web configuration.
 *
 * Security posture note (BUILD_BIBLE §11 "no secrets in client bundle"):
 * only `NEXT_PUBLIC_*` variables are readable from this app's client code. Nothing in
 * `apps/web` may read a server-only secret; the Anthropic key lives exclusively behind
 * the API's AI Gateway and never crosses this boundary.
 */

const isDev = process.env.NODE_ENV !== 'production';

/**
 * The API's absolute origin. The browser never sees it: it is the destination of the
 * `/api` rewrite below, and the base `lib/session.ts` uses for server-side calls.
 * Resolved at build time, falling back to the locked local port from the repo contract.
 */
const apiOrigin = (() => {
  const raw = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
  try {
    return new URL(raw).origin;
  } catch {
    // A malformed NEXT_PUBLIC_API_URL must not silently widen the policy to '*'.
    // Fail closed onto the documented local default and say so loudly at build time.
    console.warn(
      `[next.config] NEXT_PUBLIC_API_URL is not a valid URL (${JSON.stringify(raw)}); ` +
        'falling back to http://localhost:8000 for connect-src.',
    );
    return 'http://localhost:8000';
  }
})();

/**
 * Content-Security-Policy.
 *
 * DEV vs PROD, and why they differ:
 *
 *  - `script-src` carries `'unsafe-eval'` in development ONLY. Next's React Fast Refresh
 *    and the webpack dev runtime evaluate generated code; without it the dev server
 *    cannot hot-reload. It is stripped in production.
 *
 *  - `script-src` carries `'unsafe-inline'` in BOTH modes, which is a deliberate,
 *    documented compromise rather than an oversight. The App Router streams RSC payloads
 *    as inline `self.__next_f.push(...)` <script> tags emitted during rendering. Removing
 *    `'unsafe-inline'` requires per-request nonce propagation through `middleware.ts`,
 *    which forces every route to render dynamically. That is a Week 4 hardening item
 *    (ROADMAP W4 "Security pass"), tracked so it is not forgotten. Until then the risk is
 *    bounded by there being no user-authored HTML anywhere in this demo.
 *
 *  - `style-src` carries `'unsafe-inline'` because Next injects critical CSS inline and
 *    `next/font` writes inline @font-face declarations.
 *
 *  - `connect-src` is `'self'` and nothing more. The browser only ever calls `/api` on
 *    this origin and the rewrite forwards it, so no external origin belongs here. In dev
 *    it also allows the webpack HMR websocket.
 *
 *  - `frame-ancestors 'none'` is the CSP-level equivalent of X-Frame-Options: DENY and is
 *    the one browsers actually honour for nested contexts.
 */
const csp = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ''}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self'${isDev ? ' ws: wss:' : ''}`,
  "manifest-src 'self'",
  "worker-src 'self' blob:",
  ...(isDev ? [] : ['upgrade-insecure-requests']),
].join('; ');

/**
 * Permissions-Policy: deny everything this app has no business using. The demo needs no
 * camera, microphone, geolocation, payment or sensor access, so all are switched off.
 */
const permissionsPolicy = [
  'accelerometer=()',
  'autoplay=()',
  'camera=()',
  'display-capture=()',
  'encrypted-media=()',
  'fullscreen=(self)',
  'geolocation=()',
  'gyroscope=()',
  'magnetometer=()',
  'microphone=()',
  'midi=()',
  'payment=()',
  'publickey-credentials-get=()',
  'screen-wake-lock=()',
  'usb=()',
  'xr-spatial-tracking=()',
].join(', ');

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,

  // @naddp/contracts ships TypeScript source (no build step), so Next must compile it.
  transpilePackages: ['@naddp/contracts'],

  eslint: {
    // CI runs `pnpm lint` explicitly; keep `next build` failing on lint errors too.
    ignoreDuringBuilds: false,
  },
  typescript: {
    // Never ship a build that skipped typechecking.
    ignoreBuildErrors: false,
  },

  // `/` renders app/page.tsx, which calls redirect('/command'). Prerendered, that ships an
  // HTML shell which only navigates once React has hydrated, so the first paint is an
  // unstyled error shell. A routing-layer rule answers with a real 307 + Location before
  // any JS runs; page.tsx stays as the fallback for anything this rule does not match.
  async redirects() {
    return [{ source: '/', destination: '/command', permanent: false }];
  },

  // Every browser call goes to same-origin `/api` and is proxied from here, so the demo
  // session cookie stays same-site and no CORS preflight ever happens.
  //
  // Defined in this file rather than vercel.json on purpose: vercel.json is not read by
  // `next dev`, and the whole point of the proxy is that local and deployed behave the
  // same way. Next compiles rewrites into Vercel's routing layer, so the hop costs no
  // function invocation. There are no route handlers under app/api to shadow, and the
  // default (afterFiles) would yield to them if any were ever added.
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${apiOrigin}/:path*` }];
  },

  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'Content-Security-Policy', value: csp },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: permissionsPolicy },
          { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' },
          { key: 'X-DNS-Prefetch-Control', value: 'off' },
          // This is a synthetic-data demonstration environment. Keep it out of indexes
          // at the transport layer as well as in <meta name="robots">.
          { key: 'X-Robots-Tag', value: 'noindex, nofollow' },
        ],
      },
    ];
  },
};

export default nextConfig;
