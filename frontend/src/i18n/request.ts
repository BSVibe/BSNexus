/**
 * next-intl request config — composes the shared `@bsvibe/i18n`
 * namespaces (`common`, `auth`) with the BSNexus-local `nexus` namespace.
 *
 * BSNexus pins `defaultLocale: 'ko'` — the UI copy and Playwright e2e
 * suite assert on Korean. English is opt-in via the `/en` URL prefix
 * produced by the `localePrefix: 'as-needed'` middleware. With `as-needed`
 * + default `ko`, the bare path renders Korean and only English carries
 * a prefix.
 */
import { getRequestConfig as defineRequestConfig } from 'next-intl/server'
import {
  getRequestConfig as buildSharedConfig,
  resolveLocale,
} from '@bsvibe/i18n'

const BSNEXUS_DEFAULT_LOCALE = 'ko' as const

export default defineRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale
  const locale = resolveLocale(requested, BSNEXUS_DEFAULT_LOCALE)

  // BSNexus messages live at `frontend/messages/{en,ko}.json`, shaped
  // `{ "nexus": { ...sub-namespaces... } }`. Layering the `nexus`
  // namespace keeps every existing translation call under it working;
  // `buildSharedConfig` adds the shared top-level `common` / `auth`
  // namespaces.
  const file = (await import(`../../messages/${locale}.json`)).default

  const shared = await buildSharedConfig({
    locale,
    extra: { nexus: file.nexus },
  })

  return {
    locale,
    messages: shared.messages,
  }
})
