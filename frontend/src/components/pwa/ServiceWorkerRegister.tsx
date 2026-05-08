'use client'

import { useEffect } from 'react'

/**
 * Registers ``/sw.js`` on first mount.
 *
 * No-ops in dev (Next.js HMR doesn't play nicely with a service worker)
 * and on browsers without ``serviceWorker``. The SW itself is at
 * ``public/sw.js`` and only intercepts same-origin GETs — registering
 * it is enough to make the site count as a PWA install target on iOS
 * Safari and Chrome.
 */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof window === 'undefined') return
    if (!('serviceWorker' in navigator)) return
    if (process.env.NODE_ENV !== 'production') return

    const register = async () => {
      try {
        await navigator.serviceWorker.register('/sw.js', { scope: '/' })
      } catch {
        /* registration failure is non-fatal — the app keeps working,
           it just isn't installable on this visit. */
      }
    }
    register()
  }, [])

  return null
}
