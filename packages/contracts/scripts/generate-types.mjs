#!/usr/bin/env node
/**
 * Regenerates `src/generated/openapi.d.ts` from the live FastAPI OpenAPI document.
 *
 * Why a Node script instead of a plain package.json script string?
 * The obvious form -- `openapi-typescript ${API_URL:-http://localhost:8000}/openapi.json`
 * -- is POSIX-shell syntax. On Windows, npm/pnpm run lifecycle scripts through cmd.exe,
 * where `${VAR:-default}` is a literal string, not a defaulted variable. This repo is
 * built on Windows and shipped to Linux CI, so the script must behave identically on
 * both. Reading the env var in Node and spawning the CLI ourselves is the only form
 * that is genuinely portable without adding a cross-env style dependency.
 *
 * Usage:
 *   pnpm --filter @naddp/contracts generate
 *   API_URL=https://naddp-api.example.org pnpm --filter @naddp/contracts generate
 */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const DEFAULT_API_URL = 'http://localhost:8000';

const here = path.dirname(fileURLToPath(import.meta.url));
const packageRoot = path.resolve(here, '..');
const outFile = path.join(packageRoot, 'src', 'generated', 'openapi.d.ts');

const rawBase = process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_API_URL;
const base = rawBase.replace(/\/+$/, '');

let schemaUrl;
try {
  schemaUrl = new URL('/openapi.json', base).toString();
} catch (cause) {
  console.error(
    `[contracts] API_URL is not a valid absolute URL: ${JSON.stringify(rawBase)}. ` +
      `Expected something like ${DEFAULT_API_URL}.`,
  );
  console.error(cause instanceof Error ? cause.message : String(cause));
  process.exit(1);
}

console.log(`[contracts] generating types from ${schemaUrl}`);
console.log(`[contracts] output: ${outFile}`);

/**
 * Locate the openapi-typescript CLI entrypoint and invoke it with the current Node
 * binary. This sidesteps the `.bin` shim, which on Windows is a `.cmd` file that cannot
 * be spawned without `shell: true` (and `shell: true` reintroduces quoting hazards).
 *
 * Two strategies, in order, because a package's `exports` map is allowed to withhold
 * `./package.json`:
 *   1. read the manifest's own `bin` field, which is authoritative;
 *   2. fall back to the conventional path inside the resolvable node_modules trees.
 */
const require = createRequire(import.meta.url);

/** @returns {string | null} */
function resolveCliFromManifest() {
  try {
    const manifestPath = require.resolve('openapi-typescript/package.json');
    /** @type {{ bin?: string | Record<string, string> }} */
    const manifest = require('openapi-typescript/package.json');
    const bin = manifest.bin;
    const relative =
      typeof bin === 'string' ? bin : (bin?.['openapi-typescript'] ?? 'bin/cli.js');
    const candidate = path.resolve(path.dirname(manifestPath), relative);
    return existsSync(candidate) ? candidate : null;
  } catch {
    // The manifest is not exported by this package version. Fall through to the
    // filesystem probe below rather than failing here.
    return null;
  }
}

/** @returns {string | null} */
function resolveCliFromNodeModules() {
  const roots = [
    packageRoot,
    path.resolve(packageRoot, '..', '..'), // pnpm workspace root
  ];
  for (const root of roots) {
    const candidate = path.join(
      root,
      'node_modules',
      'openapi-typescript',
      'bin',
      'cli.js',
    );
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

const cliPath = resolveCliFromManifest() ?? resolveCliFromNodeModules();

if (cliPath === null) {
  console.error(
    '[contracts] could not locate the openapi-typescript CLI. Run `pnpm install` at the repo root first.',
  );
  process.exit(1);
}

const child = spawn(process.execPath, [cliPath, schemaUrl, '--output', outFile], {
  stdio: 'inherit',
  cwd: packageRoot,
});

child.on('error', (error) => {
  console.error(
    '[contracts] failed to launch openapi-typescript. Has `pnpm install` been run at the repo root?',
  );
  console.error(error.message);
  process.exit(1);
});

child.on('close', (code, signal) => {
  if (signal) {
    console.error(`[contracts] openapi-typescript terminated by signal ${signal}`);
    process.exit(1);
  }
  if (code !== 0) {
    console.error(
      `[contracts] openapi-typescript exited with code ${code}. ` +
        `Is the API running and serving ${schemaUrl}? Try \`make dev\` (or \`.\\make.ps1 dev\`) first.`,
    );
    process.exit(code ?? 1);
  }
  console.log('[contracts] types written.');
});
