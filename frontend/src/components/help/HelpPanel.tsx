'use client'

import { useSyncExternalStore } from 'react'
import { useTranslations } from 'next-intl'

type TopicKey = 'dashboard' | 'project' | 'settings' | 'default'

function topicForPathname(pathname: string): TopicKey {
  if (pathname === '/' || pathname.startsWith('/dashboard')) return 'dashboard'
  if (pathname.startsWith('/projects') || pathname.startsWith('/project'))
    return 'project'
  if (pathname.startsWith('/settings')) return 'settings'
  return 'default'
}

interface HelpPanelProps {
  isOpen: boolean
  onClose: () => void
}

function subscribeToPathname(callback: () => void) {
  window.addEventListener('popstate', callback)
  return () => window.removeEventListener('popstate', callback)
}

function getPathname() {
  return window.location.pathname
}

function getServerSnapshot() {
  return '/'
}

export default function HelpPanel({ isOpen, onClose }: HelpPanelProps) {
  const pathname = useSyncExternalStore(
    subscribeToPathname,
    getPathname,
    getServerSnapshot,
  )
  const tPanel = useTranslations('nexus.help.panel')
  const tTopic = useTranslations('nexus.help.topic')
  const topic = topicForPathname(pathname)
  const title = tTopic(`${topic}.title`)
  const description = tTopic(`${topic}.description`)

  return (
    <>
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40"
          onClick={onClose}
        />
      )}

      <div
        className={`fixed top-0 right-0 h-full w-80 bg-gray-900 border-l border-gray-700 z-50 shadow-2xl
          transform transition-transform duration-300 ease-in-out
          ${isOpen ? 'translate-x-0' : 'translate-x-full'}`}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <h2 className="text-lg font-semibold text-gray-50">{tPanel('title')}</h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-gray-800 text-gray-400 hover:text-gray-50 transition-colors"
            aria-label={tPanel('closeLabel')}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-5 w-5"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>

        <div className="px-5 py-6">
          <div className="mb-2">
            <span className="inline-block px-2 py-0.5 text-xs font-medium rounded bg-[#3b82f6]/20 text-[#3b82f6]">
              {title}
            </span>
          </div>
          <p className="text-gray-300 text-sm leading-relaxed">{description}</p>
        </div>
      </div>
    </>
  )
}
