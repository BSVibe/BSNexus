import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import HelpButton from '../help/HelpButton'

export default function Layout() {
  return (
    <div className="flex h-screen overflow-hidden text-text-primary bg-stitch-surface">
      <Sidebar />
      <main className="flex-1 flex flex-col min-w-0 bg-stitch-surface overflow-hidden">
        <div className="flex-1 overflow-auto">
          <Outlet />
        </div>
      </main>
      <HelpButton />
    </div>
  )
}
