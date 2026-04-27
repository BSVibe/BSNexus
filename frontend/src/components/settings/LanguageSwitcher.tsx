'use client'

import { useTranslations } from 'next-intl'

import { useLocale } from '../../i18n/LocaleContext'
import { type Locale, SUPPORTED_LOCALES } from '../../i18n'

/**
 * Settings → Language section. Switches the next-intl locale and
 * persists the choice to ``localStorage`` (see ``IntlProvider``).
 *
 * The component is intentionally unstyled chrome only — relies on the
 * existing ``card`` / ``btn`` classes from ``index.css`` so it renders
 * inside the design-tokens-driven theme without extra CSS.
 */
export default function LanguageSwitcher() {
  const t = useTranslations('nexus.settings.languageSwitcher')
  const { locale, setLocale } = useLocale()

  return (
    <div
      data-testid="language-switcher"
      className="card"
      style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}
    >
      <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
        {t('label')}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        {SUPPORTED_LOCALES.map((value: Locale) => {
          const selected = value === locale
          return (
            <button
              key={value}
              type="button"
              data-testid={`language-switcher-${value}`}
              aria-pressed={selected}
              className={`btn ${selected ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setLocale(value)}
            >
              {t(value)}
              {selected && (
                <span className="mono faded" style={{ fontSize: 11, marginLeft: 6 }}>
                  · {t('currentLabel')}
                </span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
