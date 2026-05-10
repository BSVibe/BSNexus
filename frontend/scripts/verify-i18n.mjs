#!/usr/bin/env node
/**
 * Verifies the next-intl messages bundles for BSNexus:
 *
 *   - ``messages/ko.json`` and ``messages/en.json`` exist and parse.
 *   - Both bundles share the exact same key shape (no missing translations
 *     for either locale; no orphan keys).
 *   - All keys live under the ``nexus.*`` top-level namespace per
 *     BSVibe_Shared_Library_Roadmap §C1 (each product owns its own
 *     namespace; common/auth come from the shared lib in a later sprint).
 *   - The bundle covers at least the ~179 strings target Phase C calls
 *     out for BSNexus (BSVibe_Execution_Prompt §190 + Roadmap §C3).
 *   - No retired founder-metaphor terms (Agent / Architect / Task / Phase
 *     / Goal / Kanban) leak into the message values — the migration in
 *     PR #29 deliberately removed those surfaces (CLAUDE.md NEVER rule).
 *
 * Mirrors ``verify-design-tokens.mjs`` — script, not pytest, because the
 * frontend has no JS unit-test runner today and pytest would be the wrong
 * layer regardless. CI runs this via ``pnpm i18n:verify``.
 */
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '..')
const KO_PATH = resolve(REPO_ROOT, 'messages/ko.json')
const EN_PATH = resolve(REPO_ROOT, 'messages/en.json')
const SRC_ROOT = resolve(REPO_ROOT, 'src')

// Target string count for BSNexus per Phase C plan. The exact number can
// shift slightly as surfaces evolve; we treat 179 as the floor.
const MIN_KEY_COUNT = 179

// Surfaces in scope (CLAUDE.md §Project Structure — frontend/src/components):
//   direction (single Chief-of-Staff conversation, lives in GlobalChat)
//   brief     (5 founder-cards summary surface — replaces the legacy
//              ``progress`` timeline-only namespace, locked O5 trigger
//              reached 2026-05-08 with the surface restructure)
//   decisions (approval inbox)
//   inside    (execution-run tree + composition snapshot — InsideView)
// Plus auth, common, layout, settings, dashboard, project, landing,
// help, decisionInbox (Home strip).
const REQUIRED_TOP_NAMESPACES = ['nexus']
// Greenfield surfaces only. ``chat`` (GlobalChat surface) and
// ``inside`` (Inspector + run quality / activity / failure-mode) were
// retired with the legacy purge per file-disposition.md §Frontend.
const REQUIRED_NEXUS_SUBSECTIONS = [
  'common',
  'auth',
  'layout',
  'palette',
  'dashboard',
  'project',
  'brief',
  'decisions',
  'settings',
  'landing',
]

// Retired surfaces (founder-metaphor migration, PR #29). Re-introducing
// any of these would violate CLAUDE.md NEVER rule:
//   "Never reintroduce an Agent / Architect / Task / Phase / Goal row"
// The check operates on KEY paths, not VALUES, because some legitimate
// values reference these terms (e.g. "claude-code worker" mentions
// agents conceptually). Keys are under our control.
const RETIRED_KEY_TERMS = [
  /\bagents?\b/i,
  /\barchitect\b/i,
  /\btasks?\b/i,
  /\bphases?\b/i,
  /\bgoals?\b/i,
  /\bkanban\b/i,
  /\bbudget\b/i,
  /\borg[-_]?chart\b/i,
]

function flatKeys(obj, prefix = '') {
  const out = []
  for (const [key, val] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (val && typeof val === 'object' && !Array.isArray(val)) {
      out.push(...flatKeys(val, path))
    } else {
      out.push(path)
    }
  }
  return out
}

function loadBundle(path, label) {
  if (!existsSync(path)) {
    console.error(`[verify-i18n] ${label} bundle missing: ${path}`)
    process.exit(1)
  }
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch (err) {
    console.error(`[verify-i18n] ${label} bundle is not valid JSON: ${err.message}`)
    process.exit(1)
  }
}

function hasNested(obj, path) {
  return path.split('.').reduce((cur, seg) => {
    if (cur && typeof cur === 'object' && seg in cur) return cur[seg]
    return undefined
  }, obj) !== undefined
}

// ─── Orphan-namespace lint (G7.4) ─────────────────────────────────────
// PR #93 deleted ``nexus.errors`` keys without removing the
// ``useTranslations('nexus.errors')`` consumer in IntegrationsTab. The
// site rendered fine until Settings → Integrations mounted, then the
// console exploded with ``IntlError: MISSING_MESSAGE``. This lint
// catches that regression class at PR time.
//
// Walk ``src/**/*.{ts,tsx,js,jsx}`` for every ``useTranslations(<lit>)``
// call; assert the namespace literal exists in both bundles. Template
// literals / dynamic args are skipped (too few in this codebase to
// justify AST parsing; if that changes, swap in @typescript-eslint
// AST visitor).

const _SRC_EXTENSIONS = new Set(['.ts', '.tsx', '.js', '.jsx'])
const _SKIP_DIRS = new Set(['node_modules', '.next', 'dist', 'build', '__pycache__'])
// Match ``useTranslations('foo.bar')`` or ``useTranslations("foo.bar")``.
// The namespace must be a single-quoted or double-quoted string literal
// (no template-literal interpolation). Whitespace is tolerated between
// the call and the opening paren.
const _USE_TRANSLATIONS_RE = /useTranslations\s*\(\s*(['"])((?:(?!\1).)+)\1\s*\)/g

function* _walkSourceFiles(dir) {
  for (const entry of readdirSync(dir)) {
    if (_SKIP_DIRS.has(entry)) continue
    const full = join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) {
      yield* _walkSourceFiles(full)
    } else if (_SRC_EXTENSIONS.has(full.slice(full.lastIndexOf('.')))) {
      yield full
    }
  }
}

/**
 * @returns Map<namespace, Array<{file: string, line: number}>>
 */
function collectUseTranslationsCalls(srcRoot) {
  const out = new Map()
  if (!existsSync(srcRoot)) return out
  for (const file of _walkSourceFiles(srcRoot)) {
    const text = readFileSync(file, 'utf8')
    let match
    _USE_TRANSLATIONS_RE.lastIndex = 0
    while ((match = _USE_TRANSLATIONS_RE.exec(text)) !== null) {
      const namespace = match[2]
      // Compute the 1-based line number of this match for diagnostics.
      const line = text.slice(0, match.index).split('\n').length
      const rel = relative(REPO_ROOT, file)
      const sites = out.get(namespace) ?? []
      sites.push({ file: rel, line })
      out.set(namespace, sites)
    }
  }
  return out
}

function main() {
  const ko = loadBundle(KO_PATH, 'ko')
  const en = loadBundle(EN_PATH, 'en')

  const errors = []

  // 1. Top-level namespaces present.
  for (const ns of REQUIRED_TOP_NAMESPACES) {
    if (!(ns in ko)) errors.push(`ko bundle missing top-level namespace: ${ns}`)
    if (!(ns in en)) errors.push(`en bundle missing top-level namespace: ${ns}`)
  }

  // 2. Required nexus subsections present in both bundles.
  for (const sub of REQUIRED_NEXUS_SUBSECTIONS) {
    const path = `nexus.${sub}`
    if (!hasNested(ko, path)) errors.push(`ko bundle missing nested namespace: ${path}`)
    if (!hasNested(en, path)) errors.push(`en bundle missing nested namespace: ${path}`)
  }

  // 3. Identical key shape across locales.
  const koKeys = new Set(flatKeys(ko))
  const enKeys = new Set(flatKeys(en))
  for (const k of koKeys) {
    if (!enKeys.has(k)) errors.push(`en bundle missing key present in ko: ${k}`)
  }
  for (const k of enKeys) {
    if (!koKeys.has(k)) errors.push(`ko bundle missing key present in en: ${k}`)
  }

  // 4. String count target.
  if (koKeys.size < MIN_KEY_COUNT) {
    errors.push(
      `ko bundle has ${koKeys.size} keys; expected at least ${MIN_KEY_COUNT} ` +
        `(Phase C BSNexus target per BSVibe_Execution_Prompt §190).`,
    )
  }

  // 5. No retired-surface key paths leak in.
  for (const k of koKeys) {
    for (const pattern of RETIRED_KEY_TERMS) {
      if (pattern.test(k)) {
        errors.push(
          `key "${k}" matches retired founder-metaphor term ${pattern}. ` +
            `See CLAUDE.md NEVER rule + PR #29 (founder-metaphor migration).`,
        )
      }
    }
  }

  // 6. G7.4 orphan-namespace lint — every ``useTranslations('foo.bar')``
  //    call site must reference a namespace that exists in both
  //    bundles. PR #93 regression class: deleting i18n keys without
  //    sweeping consumers blew up the Settings page at runtime.
  const callSites = collectUseTranslationsCalls(SRC_ROOT)
  let orphanCount = 0
  for (const [ns, sites] of callSites) {
    const inKo = hasNested(ko, ns)
    const inEn = hasNested(en, ns)
    if (!inKo || !inEn) {
      orphanCount += 1
      const where = sites.map((s) => `${s.file}:${s.line}`).join(', ')
      const missing = [!inKo && 'ko', !inEn && 'en'].filter(Boolean).join(' + ')
      errors.push(
        `useTranslations('${ns}') has no entry in ${missing} bundle (called from ${where}). ` +
          `Remove the consumer or add the namespace to messages/{en,ko}.json.`,
      )
    }
  }

  if (errors.length > 0) {
    console.error('[verify-i18n] FAILED')
    for (const e of errors) console.error('  -', e)
    process.exit(1)
  }

  console.log(
    `[verify-i18n] OK — ${koKeys.size} keys per locale across ` +
      `${REQUIRED_NEXUS_SUBSECTIONS.length} nexus.* subsections; ` +
      `${callSites.size} useTranslations namespaces all bound.`,
  )
}

main()
