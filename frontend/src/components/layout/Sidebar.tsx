'use client'

import { useTranslations } from 'next-intl'
import { usePathname, useRouter } from 'next/navigation'
import { useCurrentLocale } from '@bsvibe/i18n'
import {
  LanguageToggle,
  ResponsiveSidebar,
  SidebarBrand,
  SidebarTenantSwitcher,
  SidebarUserCard,
} from '@bsvibe/layout'
import type { SidebarItem } from '@bsvibe/layout'

import { StatusDot } from '../common/Badge'
import { I } from '../../lib/icons'
import { statusTone } from '../../lib/tone'
import { useAuthContext } from '../auth/AuthContext'
import { SUPPORTED_LOCALES } from '../../i18n'
import type { Project } from '../../api/projects'

interface SidebarProps {
  projects: Project[]
  onOpenPalette: () => void
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

/**
 * BSNexus sidebar — built on `@bsvibe/layout`'s ResponsiveSidebar.
 *
 * The Projects list (dynamic, with status dots) and the static
 * Workspace items (Dashboard, Settings) all live in `items`. The
 * `Projects` group is materialised via `groupLabel: 'Projects'`,
 * which the library auto-renders as a small uppercase header above
 * the first item of each contiguous group.
 *
 * The ⌘K command palette trigger is rendered as `topAction` until a
 * `<Header>` component lands; per Phase B handoff §6 Stage N the
 * search slot is meant to live in the header — this is a transitional
 * placement that keeps the palette reachable on every page.
 */
export default function Sidebar({
  projects,
  onOpenPalette,
  open,
  onOpenChange,
}: SidebarProps) {
  const { user, logout, tenants, switchTenant } = useAuthContext()
  const router = useRouter()
  const pathname = usePathname() ?? '/'
  const locale = useCurrentLocale()
  const t = useTranslations('nexus.layout')
  const tAuth = useTranslations('nexus.auth')

  // Locale switcher — mirrors BSupervisor's `SidebarLocaleSwitcher`, with
  // the default-locale direction flipped. BSNexus default is `ko`
  // (`localePrefix: 'as-needed'`), so Korean is bare-rooted and English
  // carries the `/en` prefix. Strip any leading `/ko|/en` segment, then
  // re-prefix only when switching to the non-default locale (`en`).
  const handleLocaleChange = (next: string) => {
    if (next === locale) return
    const stripped = pathname.replace(/^\/(ko|en)(?=\/|$)/, '') || '/'
    const nextPath =
      next === 'en' ? `/en${stripped === '/' ? '' : stripped}` : stripped
    router.replace(nextPath)
  }

  const items: SidebarItem[] = [
    ...projects.map<SidebarItem>((p) => ({
      href: `/projects/${p.id}`,
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <StatusDot tone={statusTone(p.status)} size={6} />
          <span>{p.name}</span>
        </span>
      ),
      groupLabel: t('projects'),
    })),
    {
      href: '/projects/new',
      label: (
        <span style={{ color: 'var(--text-tertiary)' }}>
          + {t('newProject')}
        </span>
      ),
      groupLabel: t('projects'),
    },
    {
      href: '/dashboard',
      label: t('dashboard'),
      icon: <I.Home size={14} />,
      groupLabel: t('workspace'),
    },
    {
      href: '/settings',
      label: t('settings'),
      icon: <I.Settings size={14} />,
      groupLabel: t('workspace'),
    },
  ]

  const handleSignOut = () => {
    void logout()
  }

  return (
    <ResponsiveSidebar
      items={items}
      ariaLabel={t('openNavigation')}
      open={open}
      onOpenChange={onOpenChange}
      logo={
        <SidebarBrand
          icon={<span style={{ fontWeight: 700, fontSize: 11 }}>BN</span>}
          name={t('brandName')}
          // Active workspace (tenant) name. Collapses when not known —
          // unified with Gateway / Supervisor / Sage.
          tagline={user?.tenantName ?? undefined}
          href="/dashboard"
        />
      }
      topAction={
        <button
          type="button"
          className="chip"
          style={{
            width: '100%',
            padding: '8px 10px',
            justifyContent: 'space-between',
            display: 'inline-flex',
          }}
          onClick={onOpenPalette}
          title={t('jumpAnywhere')}
        >
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              color: 'var(--text-tertiary)',
            }}
          >
            <I.Search size={14} /> {t('jumpTo')}
          </span>
          <span style={{ display: 'inline-flex', gap: 4 }}>
            <kbd>⌘</kbd>
            <kbd>K</kbd>
          </span>
        </button>
      }
      footer={
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <SidebarTenantSwitcher
            tenants={tenants}
            activeTenantId={user?.tenantId ?? null}
            onSwitchTenant={(id) => void switchTenant(id)}
            dataTestId="sidebar-tenant-switcher"
          />
          <LanguageToggle
            value={locale}
            options={SUPPORTED_LOCALES.map((l) => ({ value: l, label: l.toUpperCase() }))}
            onChange={handleLocaleChange}
            ariaLabel={t('language')}
            dataTestId="sidebar-language-switcher"
          />
          <SidebarUserCard
            email={user?.email ?? tAuth('guest')}
            role={user?.role}
            onSignOut={handleSignOut}
            signOutLabel={tAuth('logout')}
          />
        </div>
      }
    />
  )
}
