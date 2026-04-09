import { useSyncExternalStore } from 'react'

interface HelpContent {
  title: string
  description: string
  link?: { label: string; href: string }
}

function getHelpContent(pathname: string): HelpContent {
  if (pathname === '/' || pathname.startsWith('/dashboard')) {
    return {
      title: 'Dashboard',
      description:
        '프로젝트 현황, 태스크 진행률, 에이전트 비용을 한눈에 확인합니다. ' +
        'New Project를 눌러 AI Architect와 대화하며 프로젝트를 설계하세요.',
    }
  }
  if (pathname.startsWith('/agents')) {
    return {
      title: 'Agents',
      description:
        'AI 에이전트 조직도를 관리합니다. 에이전트에 역할(CTO, Engineer 등)을 부여하고, ' +
        '계층 구조를 만들고, executor를 할당합니다. 카드를 클릭하면 상세 정보를 보고 편집할 수 있습니다.',
    }
  }
  if (pathname.startsWith('/budget')) {
    return {
      title: 'Budget',
      description:
        '에이전트별 월간 예산과 비용을 추적합니다. ' +
        '각 에이전트 카드에서 예산 사용률을 확인하고, Cost Records에서 상세 내역을 볼 수 있습니다. ' +
        '월말에 Reset Monthly로 사용량을 초기화하세요.',
    }
  }
  if (pathname.startsWith('/settings')) {
    return {
      title: 'Settings',
      description:
        'LLM API 키, 모델, Base URL을 설정합니다. ' +
        'Default Executor를 선택하면 새 에이전트 생성 시 기본값으로 사용됩니다. ' +
        'Executor별 상세 설정은 각 에이전트의 편집 모드에서 할 수 있습니다.',
    }
  }
  if (pathname.startsWith('/architect')) {
    return {
      title: 'AI Architect',
      description:
        'AI Architect와 대화하며 프로젝트를 설계합니다. ' +
        '요구사항을 설명하면 Phase와 Task로 분해하고, 코드 구조를 제안합니다.',
    }
  }
  if (pathname.startsWith('/project')) {
    return {
      title: 'Project',
      description:
        '프로젝트의 태스크를 Kanban 보드와 Timeline으로 관리합니다. ' +
        'Board 뷰에서 드래그 앤 드롭, Timeline 뷰에서 일정을 확인하세요.',
    }
  }
  return {
    title: 'BSNexus Company OS',
    description:
      'AI 에이전트 조직을 구성하고 프로젝트를 자동화하는 Company OS입니다. ' +
      'Dashboard에서 전체 현황을, Agents에서 조직도를, Budget에서 비용을 관리하세요.',
  }
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

export default function HelpPanel({ isOpen, onClose }: HelpPanelProps) {
  const pathname = useSyncExternalStore(subscribeToPathname, getPathname)
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
