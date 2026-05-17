/**
 * i18n parity check — ``messages/en.json`` and ``messages/ko.json`` must
 * have an identical key tree, and every leaf must be a non-empty string.
 *
 * This is a pure data test (no browser needed) — it imports both bundles
 * directly. It complements ``scripts/verify-i18n.mjs`` (run via
 * ``pnpm i18n:verify``), which additionally lints retired-surface key
 * paths and binds ``useTranslations`` call sites. Keeping a Playwright
 * spec here means the parity gate also runs in the default e2e suite.
 *
 * Catches the regression class where a translator adds a key to one
 * locale and forgets the other — next-intl renders the raw key path
 * with no build error.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve as resolvePath } from 'node:path'

import { test, expect } from '@playwright/test'

type Json = string | number | boolean | null | JsonObject | Json[]
interface JsonObject {
  [key: string]: Json
}

// Load the bundles via ``readFileSync`` rather than an ``import`` so the
// spec is independent of the test runner's JSON-import-attribute support
// (Playwright's ESM loader rejects a bare ``import x from './x.json'``).
const __dirname = dirname(fileURLToPath(import.meta.url))
const MESSAGES_DIR = resolvePath(__dirname, '../../messages')
const en = JSON.parse(
  readFileSync(resolvePath(MESSAGES_DIR, 'en.json'), 'utf8'),
) as JsonObject
const ko = JSON.parse(
  readFileSync(resolvePath(MESSAGES_DIR, 'ko.json'), 'utf8'),
) as JsonObject

/** Flatten a nested object into dotted ``a.b.c`` leaf paths. */
function flatKeys(obj: JsonObject, prefix = ''): string[] {
  const out: string[] = []
  for (const [key, val] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (val && typeof val === 'object' && !Array.isArray(val)) {
      out.push(...flatKeys(val as JsonObject, path))
    } else {
      out.push(path)
    }
  }
  return out
}

/** Resolve a dotted path to its leaf value. */
function resolve(obj: JsonObject, path: string): Json {
  return path
    .split('.')
    .reduce<Json>((cur, seg) => {
      if (cur && typeof cur === 'object' && !Array.isArray(cur)) {
        return (cur as JsonObject)[seg]
      }
      return undefined as unknown as Json
    }, obj)
}

test.describe('i18n message bundle parity', () => {
  const enKeys = flatKeys(en)
  const koKeys = flatKeys(ko)

  test('en and ko share an identical key tree', () => {
    const enSet = new Set(enKeys)
    const koSet = new Set(koKeys)
    const missingInKo = enKeys.filter((k) => !koSet.has(k))
    const missingInEn = koKeys.filter((k) => !enSet.has(k))
    expect(missingInKo, `keys present in en but missing in ko: ${missingInKo.join(', ')}`).toEqual([])
    expect(missingInEn, `keys present in ko but missing in en: ${missingInEn.join(', ')}`).toEqual([])
  })

  test('every leaf in both bundles is a non-empty string', () => {
    for (const [label, bundle, keys] of [
      ['en', en, enKeys],
      ['ko', ko, koKeys],
    ] as const) {
      for (const key of keys) {
        const value = resolve(bundle, key)
        expect(typeof value, `${label}.${key} should be a string`).toBe('string')
        expect(
          (value as string).trim().length,
          `${label}.${key} should not be empty`,
        ).toBeGreaterThan(0)
      }
    }
  })
})
