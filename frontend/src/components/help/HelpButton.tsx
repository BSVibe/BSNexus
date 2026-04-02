import { useState } from 'react'
import HelpPanel from './HelpPanel'

export default function HelpButton() {
  const [isOpen, setIsOpen] = useState(false)

  return (
    <>
      <button
        onClick={() => setIsOpen((prev) => !prev)}
        className="fixed bottom-6 right-6 z-50 w-12 h-12 rounded-full
          bg-[#3b82f6] hover:bg-blue-500 text-white
          shadow-lg hover:shadow-xl
          flex items-center justify-center
          transition-all duration-200 ease-in-out
          focus:outline-none focus:ring-2 focus:ring-[#3b82f6] focus:ring-offset-2 focus:ring-offset-gray-900"
        aria-label="도움말 열기"
      >
        <span className="text-xl font-bold">?</span>
      </button>
      <HelpPanel isOpen={isOpen} onClose={() => setIsOpen(false)} />
    </>
  )
}
