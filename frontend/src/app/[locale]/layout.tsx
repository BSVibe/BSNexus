import type { Metadata, Viewport } from 'next'
import { JetBrains_Mono, Plus_Jakarta_Sans } from 'next/font/google'
import { notFound } from 'next/navigation'
import { getMessages, setRequestLocale } from 'next-intl/server'
import { BSVibeIntlProvider, isSupportedLocale } from '@bsvibe/i18n'

import './globals.css'
import Providers from './providers'
import { ServiceWorkerRegister } from '../../components/pwa/ServiceWorkerRegister'

const plusJakartaSans = Plus_Jakarta_Sans({
  subsets: ['latin'],
  weight: 'variable',
  style: ['normal', 'italic'],
  variable: '--font-plus-jakarta-sans',
  display: 'swap',
})

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  weight: 'variable',
  style: ['normal', 'italic'],
  variable: '--font-jetbrains-mono',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'BSNexus',
  description:
    'BSNexus is the command layer for AI-native companies. AI handles the work. You make the decisions.',
  manifest: '/manifest.webmanifest',
  applicationName: 'BSNexus',
  appleWebApp: {
    capable: true,
    title: 'BSNexus',
    statusBarStyle: 'black-translucent',
  },
  icons: {
    icon: [{ url: '/favicon.svg', type: 'image/svg+xml' }],
    apple: [{ url: '/favicon.svg' }],
  },
}

// Decision-locks O2 — mobile web/PWA is the first computer-independent
// interface. ``viewport-fit=cover`` lets us read iOS safe-area insets
// (notch / home indicator) so the chat FAB and Decision Inbox strip
// don't end up under the status bar or the home pill.
export const viewport: Viewport = {
  themeColor: '#0b0d12',
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
}

// next-intl `[locale]` segment — middleware (`localePrefix: 'as-needed'`,
// default `ko`) routes Korean at the bare path and English under `/en`.
export function generateStaticParams() {
  return [{ locale: 'ko' }, { locale: 'en' }]
}

/**
 * Root layout for BSNexus — owns ``<html>`` because the next-intl
 * ``[locale]`` pattern makes ``app/[locale]/layout.tsx`` the root layout
 * (there is no ``app/layout.tsx``). It keeps everything the legacy root
 * layout had — ``next/font`` variables, metadata, viewport, the Material
 * Symbols stylesheet link, ``<ServiceWorkerRegister/>`` — and adds the
 * next-intl plumbing: locale guard, ``setRequestLocale``, message load,
 * and ``BSVibeIntlProvider``.
 *
 * ``BSVibeIntlProvider`` wraps ``Providers`` so next-intl context exists
 * before anything inside the auth/query boundary renders.
 */
export default async function RootLayout({
  children,
  params,
}: {
  children: React.ReactNode
  params: Promise<{ locale: string }>
}) {
  const { locale } = await params
  if (!isSupportedLocale(locale)) {
    notFound()
  }
  // Tell next-intl which locale this server render is for so any RSC
  // `getTranslations()` calls in nested layouts/pages resolve correctly.
  setRequestLocale(locale)
  const messages = await getMessages()

  return (
    <html
      lang={locale}
      suppressHydrationWarning
      className={`${plusJakartaSans.variable} ${jetbrainsMono.variable}`}
    >
      <head>
        <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* Material Symbols Outlined needs the FILL variable axis (the
            `material-symbols-outlined` class flips fontVariationSettings to
            'FILL' 1 for solid icons). next/font/google does not yet export
            Material_Symbols_Outlined, so this stylesheet stays as a runtime
            link until either (a) next/font lands the export with axis support
            or (b) the icon set moves off Material Symbols entirely. */}
        {/* eslint-disable-next-line @next/next/no-page-custom-font */}
        <link
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap"
          rel="stylesheet"
        />
      </head>
      <body suppressHydrationWarning>
        <BSVibeIntlProvider locale={locale} messages={messages}>
          <Providers>{children}</Providers>
        </BSVibeIntlProvider>
        <ServiceWorkerRegister />
      </body>
    </html>
  )
}
