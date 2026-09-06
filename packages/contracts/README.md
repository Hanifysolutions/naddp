# `@naddp/contracts`

The typed seam between `apps/web` and the FastAPI API.

- `src/generated/openapi.d.ts` — **generated**. Regenerate with `make gen-client`
  (`.\make.ps1 gen-client` on Windows), never by hand. A placeholder is committed so the
  workspace typechecks before the API has ever been run.
- `src/index.ts` — `createApiClient(baseUrl)`, a typed `openapi-fetch` client with
  `credentials: 'include'` so the signed `naddp_demo_session` cookie rides along; plus
  the `ApiError` shape and the role / classification enumerations.

This package is **source-only**: no build step, no `dist/`. Consumers compile it through
Next's `transpilePackages`, so there is exactly one compilation of these files and no
stale artefact to drift from the generated types.

## Regenerating

```bash
pnpm --filter @naddp/contracts generate
API_URL=https://naddp-api.example.org pnpm --filter @naddp/contracts generate
```

`scripts/generate-types.mjs` reads `API_URL` (falling back to `NEXT_PUBLIC_API_URL`, then
`http://localhost:8000`) in Node rather than in the shell. The shell form
`${API_URL:-...}` is POSIX-only and is a literal string under cmd.exe, which npm/pnpm use
for lifecycle scripts on Windows; this repo is built on Windows and shipped to Linux CI,
so the script must behave identically on both.
