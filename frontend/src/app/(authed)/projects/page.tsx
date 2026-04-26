import { Suspense } from 'react'
import ProjectPage from '../../../components/pages/ProjectPage'

/**
 * Bare ``/projects`` (no id) — matches the React-Router optional param
 * ``/projects/:projectId?`` previously rendered by the same component.
 * The component itself shows a "No project selected" placeholder when
 * ``projectId`` is undefined.
 */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <ProjectPage />
    </Suspense>
  )
}
