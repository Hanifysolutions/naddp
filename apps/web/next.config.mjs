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
 * The API origin the browser is allowed to talk to. Resolved at build time so the CSP
 * `connect-src` is accurate for whichever environment we ship. Falls back to the locked
 * local port from the repo contract.
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
 *  - `connect-src` is pinned to self plus the resolved API origin; in dev it also allows
 *    the webpack HMR websocket.
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
  `connect-src 'self' ${apiOrigin}${isDev ? ' ws: wss:' : ''}`,
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
