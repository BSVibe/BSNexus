'use client'

import { useEffect } from 'react'
import { useTranslations } from 'next-intl'
import { useRouter } from 'next/navigation'
import { useAuthContext } from '../auth/AuthContext'

const FEATURE_KEYS = ['planning', 'live', 'workers'] as const
const FEATURE_ICONS: Record<(typeof FEATURE_KEYS)[number], string> = {
  planning: 'psychology',
  live: 'account_tree',
  workers: 'hub',
}

export default function LandingPage() {
  const { user, login } = useAuthContext()
  const router = useRouter()
  const t = useTranslations('nexus.landing')
  const tAuth = useTranslations('nexus.auth')
  const tLayout = useTranslations('nexus.layout')

  useEffect(() => {
    if (user) {
      router.replace('/dashboard')
    }
  }, [user, router])

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-stitch-surface relative overflow-hidden px-4 py-6 sm:px-6">
      <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] rounded-full pointer-events-none"
        style={{ background: 'radial-gradient(circle, rgba(77,142,255,0.08) 0%, rgba(77,142,255,0.02) 40%, transparent 70%)' }}
      />

      <div className="relative z-10 w-full max-w-xl mx-auto">
        <div className="bg-stitch-surface-container border border-stitch-outline-variant/10 rounded-xl p-5 shadow-2xl shadow-black/40 sm:p-10">
          <div className="flex flex-col items-center mb-6 sm:mb-8">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-stitch-primary to-stitch-primary-container flex items-center justify-center shadow-lg shadow-stitch-primary/20 mb-4 sm:w-14 sm:h-14 sm:mb-5">
              <span className="material-symbols-outlined text-stitch-on-primary-container text-3xl" style={{ fontVariationSettings: "'FILL' 1" }}>
                architecture
              </span>
            </div>
            <h1 className="text-4xl font-extrabold text-white">{tLayout('brandName')}</h1>
            <p className="text-text-secondary mt-2 text-center text-sm leading-6 sm:text-base">
              {t('tagline')}
            </p>
            <p className="text-text-tertiary mt-2 text-center text-xs leading-5 sm:text-sm">
              {t('subTagline')}
            </p>
          </div>

          <div className="grid grid-cols-1 gap-3 mb-6 sm:grid-cols-3 sm:gap-4 sm:mb-8">
            {FEATURE_KEYS.map((key) => (
              <div key={key} className="flex items-center gap-3 text-left p-3 rounded-lg bg-stitch-surface-low sm:flex-col sm:text-center">
                <div className="w-10 h-10 rounded-lg bg-stitch-primary/10 flex shrink-0 items-center justify-center sm:mb-2">
                  <span className="material-symbols-outlined text-stitch-primary" style={{ fontSize: '20px' }}>{FEATURE_ICONS[key]}</span>
                </div>
                <div className="min-w-0">
                  <span className="block text-sm font-medium text-white mb-1">{t(`feature.${key}.title`)}</span>
                  <span className="block text-xs text-text-tertiary leading-relaxed">{t(`feature.${key}.description`)}</span>
                </div>
              </div>
            ))}
          </div>

          <div className="flex flex-col items-center">
            {user ? (
              <button
                onClick={() => router.push('/dashboard')}
                className="w-full py-3 bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container rounded-lg text-base font-bold transition-all shadow-lg shadow-stitch-primary/25 hover:opacity-90"
              >
                {t('ctaGo')}
              </button>
            ) : (
              <>
                <button
                  onClick={login}
                  className="w-full py-3 bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container rounded-lg text-base font-bold transition-all shadow-lg shadow-stitch-primary/25 hover:opacity-90"
                >
                  {tAuth('signInWithBsvibe')}
                </button>
                <p className="text-center text-sm text-text-secondary mt-4">
                  {tAuth('noAccount')}{' '}
                  <button
                    onClick={login}
                    className="inline-flex min-h-11 items-center text-stitch-primary hover:text-stitch-primary-container font-medium transition-colors"
                  >
                    {tAuth('signUp')}
                  </button>
                </p>
              </>
            )}
          </div>
        </div>

        <p className="text-center text-text-muted text-xs mt-6">
          {t('footer')}
        </p>
      </div>
    </div>
  )
}
