#!/usr/bin/env node
/**
 * Verifies frontend/src/design-tokens.ts matches /Users/blasin/Docs/design_system.md.
 * Parses the markdown spec, compares the hex values, exits non-zero on drift.
 *
 * Runs in CI via `pnpm tokens:verify`.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const SPEC_PATH = resolve(process.env.HOME || '/Users/blasin', 'Docs/design_system.md');
const TOKENS_PATH = resolve(REPO_ROOT, 'frontend/src/design-tokens.ts');

function parseSpec(markdown) {
  const expected = { gray: {}, brand: {}, radius: {} };

  for (const match of markdown.matchAll(/gray-(\d+):\s*(#[0-9a-f]+)/gi)) {
    expected.gray[match[1]] = match[2].toLowerCase();
  }

  const brandNames = ['indigo', 'blue', 'amber', 'rose', 'emerald'];
  for (const name of brandNames) {
    const match = markdown.match(new RegExp(`^\\s*${name}:\\s*(#[0-9a-f]+)`, 'im'));
    if (match) expected.brand[name] = match[1].toLowerCase();
  }

  for (const match of markdown.matchAll(/radius-(\w+)\s*\|\s*(\d+px|\d+)/g)) {
    const val = match[2];
    expected.radius[match[1]] = /^\d+$/.test(val) ? `${val}px` : val;
  }

  return expected;
}

async function loadTokens() {
  const mod = await import(pathToFileURL(TOKENS_PATH).href);
  return { gray: mod.gray, brand: mod.brand, radius: mod.radius };
}

function compare(label, expected, actual) {
  const errors = [];
  for (const key of Object.keys(expected)) {
    const got = actual[key];
    const want = expected[key];
    if (got === undefined) {
      errors.push(`  ${label}.${key}: MISSING in tokens.ts (spec says ${want})`);
    } else if (String(got).toLowerCase() !== String(want).toLowerCase()) {
      errors.push(`  ${label}.${key}: got ${got}, spec says ${want}`);
    }
  }
  return errors;
}

async function main() {
  const markdown = readFileSync(SPEC_PATH, 'utf8');
  const expected = parseSpec(markdown);
  const actual = await loadTokens();

  const errors = [
    ...compare('gray', expected.gray, actual.gray),
    ...compare('brand', expected.brand, actual.brand),
    ...compare('radius', expected.radius, actual.radius),
  ];

  if (errors.length) {
    console.error(`design-tokens.ts drift from design_system.md:`);
    for (const line of errors) console.error(line);
    process.exit(1);
  }
  console.log(`design-tokens.ts matches design_system.md (${Object.keys(expected.gray).length} gray + ${Object.keys(expected.brand).length} brand + ${Object.keys(expected.radius).length} radius)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
