import { useEffect, useState } from 'react'

interface HelpContent {
  title: string
  description: string
  link?: { label: string; href: string }
}

function getHelpContent(pathname: string): HelpContent {
  if (pathname === '/' || pathname.startsWith('/dashboard')) {
    return {
      title: '대시보드',
      description: '프로젝트 목록을 확인하고 새 프로젝트를 만듭니다.',
      link: { label: '문서 보기', href: 'https://bsvibe.dev/bsnexus/getting-started' },
    }
  }
  if (pathname.startsWith('/architect')) {
    return {
      title: 'AI Architect',
      description: 'AI Architect와 대화하며 프로젝트를 설계합니다.',
    }
  }
  if (pathname.startsWith('/board')) {
    return {
      title: 'Kanban 보드',
      description: 'Kanban 보드에서 태스크 진행 상황을 확인합니다.',
      link: { label: '문서 보기', href: 'https://bsvibe.dev/bsnexus/features/kanban' },
    }
  }
  if (pathname.startsWith('/project')) {
    return {
      title: '프로젝트 상세',
      description: '프로젝트 상세 정보를 확인합니다.',
    }
  }
  return {
    title: 'BSNexus',
    description: 'BSNexus는 AI 에이전트 오케스트레이션 플랫폼입니다.',
  }
}

interface HelpPanelProps {
  isOpen: boolean
  onClose: () => void
}

export default function HelpPanel({ isOpen, onClose }: HelpPanelProps) {
  const [pathname, setPathname] = useState(window.location.pathname)

  useEffect(() => {
    const handlePopState = () => setPathname(window.location.pathname)
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  useEffect(() => {
    if (isOpen) {
      setPathname(window.location.pathname)
    }
  }, [isOpen])

  const content = getHelpContent(pathname)

  return (
    <>
      {/* Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40"
          onClick={onClose}
        />
      )}

      {/* Panel */}
      <div
        className={`fixed top-0 right-0 h-full w-80 bg-gray-900 border-l border-gray-700 z-50 shadow-2xl
          transform transition-transform duration-300 ease-in-out
          ${isOpen ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <h2 className="text-lg font-semibold text-gray-50">도움말</h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-gray-800 text-gray-400 hover:text-gray-50 transition-colors"
            aria-label="닫기"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="px-5 py-6">
          <div className="mb-2">
            <span className="inline-block px-2 py-0.5 text-xs font-medium rounded bg-[#3b82f6]/20 text-[#3b82f6]">
              {content.title}
            </span>
          </div>
          <p className="text-gray-300 text-sm leading-relaxed mb-4">
            {content.description}
          </p>
          {content.link && (
            <a
              href={content.link.href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm font-medium text-[#3b82f6] hover:text-blue-400 transition-colors"
            >
              {content.link.label}
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                <path d="M11 3a1 1 0 100 2h2.586l-6.293 6.293a1 1 0 101.414 1.414L15 6.414V9a1 1 0 102 0V4a1 1 0 00-1-1h-5z" />
                <path d="M5 5a2 2 0 00-2 2v8a2 2 0 002 2h8a2 2 0 002-2v-3a1 1 0 10-2 0v3H5V7h3a1 1 0 000-2H5z" />
              </svg>
            </a>
          )}
        </div>
      </div>
    </>
  )
}
