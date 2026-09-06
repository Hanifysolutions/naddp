# `@naddp/web`

The Next.js 14 (App Router) surface of the **Nigeria–Australia Digital Diplomacy
Platform** Ambassador demo: the command-centre shell, the design system, and the typed
seam to the FastAPI backend.

This app is a production skeleton wearing demo clothes. Nothing in it is throwaway.

---

## Running it

Everything is driven from the repo root. `make` is not installed on Windows, so a
PowerShell shim exposes the identical targets.

```bash
# Linux / macOS / CI
make dev            # boots db + api + web
make gen-client     # regenerates packages/contracts from the live OpenAPI document

# Windows
.\make.ps1 dev
.\make.ps1 gen-client
```

Direct, once dependencies are installed at the repo root:

```bash
pnpm --filter @naddp/web dev        # http://localhost:3000
pnpm --filter @naddp/web build
pnpm --filter @naddp/web lint       # zero warnings tolerated
pnpm --filter @naddp/web typecheck  # tsc --noEmit
```

The API must be running on `http://localhost:8000` for the app to resolve a session or
load any data. Without it the shell still renders, the DEMO badge is still present, and
every figure shows as explicitly unavailable — which is the intended behaviour, not a
degraded one.

---

## The one hard rule about environment variables

> **`NEXT_PUBLIC_*` is the only environment surface that reaches the browser, and
> `src/lib/api.ts` is the only place this app reads one.**

Next inlines `process.env.NEXT_PUBLIC_*` into the client bundle at build time. Reading
any other variable in `apps/web` would ship it to every browser that loads the demo, in
violation of BUILD_BIBLE §11 ("no secrets in client bundle").

| Variable | Reaches browser | Owner |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | yes | web |
| `ANTHROPIC_API_KEY` | **never** | API, behind the AI Gateway only |
| `DEMO_SESSION_SECRET` | **never** | API (signs the demo session cookie) |
| `DATABASE_URL` | **never** | API |

The demo session cookie (`naddp_demo_session`) is signed by the API and validated by the
API. This app forwards it and reads the API's answer; it never inspects or mints one.
All model access goes through `apps/api/app/ai/gateway.py`, the only module in the repo
permitted to import the Anthropic SDK.

---

## Design system rules

**Calm.** A career diplomat reads this screen for hours. Deep navy carries structure and
hierarchy. Colour is spent only where it encodes meaning, never for decoration:

| Token | Means |
| --- | --- |
| `primary` — deep navy | structure, navigation, the application's own voice |
| `success` — deep green | a confirmed, completed or approved outcome |
| `warning` — dark amber | **awaiting human approval** — the product's core trust signal |
| `destructive` — deep red | genuine risk or denial, and nothing else |
| `demo` — bright amber | the DEMO / SYNTHETIC DATA marker, and nothing else |

Colour is never the sole carrier of meaning (WCAG 1.4.1). Every state also carries an
icon *and* a text label. The active nav item is marked by a bar, a background tint, and
`aria-current="page"` — three independent cues.

**Dense.** Tight leading, a small type step, `font-variant-numeric: tabular-nums` on every
metric so numeric columns do not jitter. The executive grid is 12 columns above 1280px,
6 between 768 and 1280, and 1 below. Verified at both rehearsal targets: at 1920×1080 the
whole board is three tiles wide and two rows deep with no scrolling; at 1440×900 the top
row is 6/3/3 and the second 4/4/4, and every metric row still fits three columns.

**AA.** WCAG 2.2 Level AA is a hard requirement, not an aspiration. Every token pair was
computed rather than eyeballed; the full table lives in the header comment of
`src/app/globals.css`. Highlights:

| Pair | Light | Dark | Needs |
| --- | --- | --- | --- |
| `foreground` on `card` | 16.57:1 | 14.87:1 | 4.5:1 |
| `muted-foreground` on `card` | 7.15:1 | 7.51:1 | 4.5:1 |
| `primary-foreground` on `primary` | 11.73:1 | 8.28:1 | 4.5:1 |
| `warning-foreground` on `warning` | 7.08:1 | 9.98:1 | 4.5:1 |
| `destructive` on `card` | 7.57:1 | 5.55:1 | 4.5:1 |
| `demo-foreground` on `demo` | 7.45:1 | 8.55:1 | 4.5:1 |
| `input` (control boundary) on `card` | 3.45:1 | 3.25:1 | 3:1 |
| `ring` (focus indicator) on `card` | 8.46:1 | 6.65:1 | 3:1 |

`--border` is intentionally low contrast (~1.4:1); it draws decorative separators only.
Anything that identifies an *interactive* control uses `--input` or `--ring`.

Other accessibility commitments enforced in the shell:

- a skip link is the first focusable element on every page;
- `:focus-visible` is defined once globally, so no component can ship without a ring;
- `<main>` is programmatically focusable, so the skip link moves focus and not just
  scroll position;
- zoom is never blocked (`maximumScale: 5`);
- `prefers-reduced-motion` disables every animation.

**Honest.** No lorem ipsum, and no invented numbers, anywhere. A tile is in exactly one
of five states — `loading`, `ready`, `empty`, `error`, `forbidden` — and an unavailable
figure renders as an em dash with a screen-reader announcement, never as `0`. A failed
session lookup says so; it is never allowed to look like a legitimately empty account.

---

## Layout of the source

```
src/
  app/
    layout.tsx            root layout: fonts, metadata (noindex), providers
    providers.tsx         TanStack Query + Radix TooltipProvider
    globals.css           design tokens, both themes, measured contrast table
    page.tsx              redirects to /command
    error.tsx             route error boundary (shows digest, offers retry)
    not-found.tsx         404, deliberately indistinguishable from 403
    command/
      layout.tsx          resolves the session, renders the AppShell
      page.tsx            the six executive tiles
      loading.tsx         same grid, same definitions, loading state
  components/
    ui/                   shadcn/ui "new-york" primitives (Radix + cva)
    layout/               app-shell, demo-badge, role-picker, nav-icons
    command/              command-tile + the tile definitions
  lib/
    api.ts                the only reader of NEXT_PUBLIC_*
    nav.ts                permission-driven navigation catalogue
    session.ts            server-side session resolution (fails closed)
    utils.ts              cn()
```

### Navigation is generated, never hard-coded

`src/lib/nav.ts` declares one required permission per destination.
`navItemsForPermissions()` returns the intersection of that catalogue and the permissions
the API reports. There is no per-persona branch anywhere in this app, and there must
never be one — a persona is an accident of which permissions a role holds.

The client-side filter is **cosmetic**. The API re-checks authorisation on every request
and is the only authority. Hiding a link is a courtesy, not a control.

### Types come from the API, not from here

`packages/contracts` holds the generated OpenAPI types and the typed `openapi-fetch`
client. A placeholder `openapi.d.ts` is committed so the workspace typechecks on a fresh
clone; until `make gen-client` has run against a live API, calling `api.GET('/v1/...')` is
a compile error rather than a silent `any`. That is the intended failure mode.

---

## Security headers

`next.config.mjs` sets CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy`, a deny-everything `Permissions-Policy`, `Cross-Origin-Opener-Policy`
and `X-Robots-Tag: noindex`.

Two CSP notes, both deliberate and documented at the call site:

1. `'unsafe-eval'` is present in **development only** (React Fast Refresh needs it) and
   stripped in production.
2. `'unsafe-inline'` remains in `script-src` in **both** modes, because the App Router
   streams RSC payloads as inline `self.__next_f.push(...)` scripts. Removing it requires
   per-request nonce propagation through `middleware.ts`, which forces every route to
   render dynamically. That is tracked as a Week 4 hardening item (ROADMAP W4, "Security
   pass"), not an oversight.
