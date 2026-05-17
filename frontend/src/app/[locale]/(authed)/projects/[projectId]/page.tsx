import { Suspense } from 'react'
import ProjectPage from '../../../../../components/pages/ProjectPage'

/**
 * ``/projects/<id>`` — resolves the dynamic ``projectId`` segment
 * inside the client component via ``useParams()`` from
 * ``next/navigation``. Wrapped in Suspense because the component
 * also reads ``useSearchParams()`` for ``?tab=`` and
 * ``?focusRequest=`` deep links.
 */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <ProjectPage />
    </Suspense>
  )
}
