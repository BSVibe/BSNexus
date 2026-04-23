import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import Header from '../components/layout/Header'
import { projectsApi, type Project } from '../api/projects'

export default function DashboardPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [creatorOpen, setCreatorOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const { data: projects = [], isLoading } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  const createMutation = useMutation({
    mutationFn: () => projectsApi.create({ name: name.trim(), description }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      setCreatorOpen(false)
      setName('')
      setDescription('')
      navigate(`/projects/${project.id}`)
    },
  })

  return (
    <>
      <Header
        title="Dashboard"
        action={
          <button
            type="button"
            onClick={() => setCreatorOpen((v) => !v)}
            className="rounded bg-accent px-3 py-1 text-sm font-semibold text-bg-primary"
          >
            {creatorOpen ? 'Cancel' : 'New project'}
          </button>
        }
      />

      <div className="p-6">
        <div className="mx-auto max-w-3xl space-y-6">
          {creatorOpen && (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                if (name.trim()) createMutation.mutate()
              }}
              className="rounded-lg border border-border bg-bg-card p-4 space-y-3"
            >
              <label className="block">
                <span className="mb-1 block text-xs text-text-tertiary">Name</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full rounded border border-border bg-bg-input px-3 py-1.5 text-sm text-text-primary"
                  placeholder="e.g. Ship landing page"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-text-tertiary">Description</span>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={3}
                  className="w-full rounded border border-border bg-bg-input px-3 py-1.5 text-sm text-text-primary"
                  placeholder="Optional"
                />
              </label>
              <button
                type="submit"
                disabled={!name.trim() || createMutation.isPending}
                className="rounded bg-accent px-3 py-1 text-sm font-semibold text-bg-primary disabled:opacity-40"
              >
                {createMutation.isPending ? 'Creating…' : 'Create'}
              </button>
              {createMutation.isError && (
                <p className="text-xs text-error">
                  {(createMutation.error as Error).message}
                </p>
              )}
            </form>
          )}

          <section>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-text-tertiary">
              Projects
            </h2>

            {isLoading ? (
              <p className="text-sm text-text-tertiary">Loading…</p>
            ) : projects.length === 0 ? (
              <div className="rounded-lg border border-border bg-bg-card p-6 text-sm text-text-tertiary">
                No projects yet. Create one above to start.
              </div>
            ) : (
              <ul className="space-y-2">
                {projects.map((project) => (
                  <li key={project.id}>
                    <Link
                      to={`/projects/${project.id}`}
                      className="block rounded-lg border border-border bg-bg-card p-4 transition-colors hover:border-accent"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-base font-semibold text-text-primary">
                          {project.name}
                        </span>
                        <span className="rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wider text-text-tertiary">
                          {project.status}
                        </span>
                      </div>
                      {project.description && (
                        <p className="mt-1 text-xs text-text-tertiary">
                          {project.description}
                        </p>
                      )}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </>
  )
}
