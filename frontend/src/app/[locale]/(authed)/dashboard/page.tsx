import { Suspense } from 'react'
import DashboardPage from '../../../../components/pages/DashboardPage'

export default function Page() {
  // Suspense is required: DashboardPage calls useSearchParams() to
  // read ?new=1 from the command palette deep-link.
  return (
    <Suspense fallback={null}>
      <DashboardPage />
    </Suspense>
  )
}
