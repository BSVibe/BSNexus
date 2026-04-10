import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { channelsApi, type ProjectChannel } from '../../api/channels'
import { Modal } from '../common'

interface ProjectChannelsModalProps {
  open: boolean
  projectId: string
  onClose: () => void
}

export default function ProjectChannelsModal({ open, projectId, onClose }: ProjectChannelsModalProps) {
  const queryClient = useQueryClient()
  const channelsQuery = useQuery({
    queryKey: ['project-channels', projectId],
    queryFn: () => channelsApi.list(projectId),
    enabled: open,
  })

  const [kind, setKind] = useState<'slack'>('slack')
  const [externalId, setExternalId] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [botToken, setBotToken] = useState('')

  const reset = () => {
    setExternalId('')
    setDisplayName('')
    setBotToken('')
  }

  const create = useMutation({
    mutationFn: () =>
      channelsApi.create(projectId, {
        kind,
        external_channel_id: externalId.trim(),
        display_name: displayName.trim() || null,
        credentials: botToken ? { bot_token: botToken } : undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project-channels', projectId] })
      reset()
    },
  })

  const remove = useMutation({
    mutationFn: (channelId: string) => channelsApi.remove(projectId, channelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project-channels', projectId] })
    },
  })

  const toggleActive = useMutation({
    mutationFn: (channel: ProjectChannel) =>
      channelsApi.update(projectId, channel.id, { is_active: !channel.is_active }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project-channels', projectId] })
    },
  })

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    if (!externalId.trim()) return
    create.mutate()
  }

  if (!open) return null

  return (
    <Modal open={open} onClose={onClose} title="Project channels" width={560}>
      <div className="space-y-6 p-1">
        <p className="text-xs text-text-tertiary">
          Connect this project's chat to external channels. The fan-out worker streams
          every assistant message to every active channel.
        </p>

        {/* Existing channels */}
        <section>
          <h3 className="text-[10px] font-bold uppercase tracking-widest text-text-tertiary mb-2">
            Linked channels
          </h3>
          {channelsQuery.isLoading ? (
            <p className="text-xs text-text-tertiary">Loading…</p>
          ) : channelsQuery.data && channelsQuery.data.length > 0 ? (
            <ul className="flex flex-col gap-1">
              {channelsQuery.data.map((c) => (
                <li
                  key={c.id}
                  className="flex items-center gap-3 rounded-md border border-stitch-outline-variant/15 bg-stitch-surface-low px-3 py-2"
                >
                  <span className="text-[10px] font-bold uppercase tracking-wider text-text-tertiary">
                    {c.kind}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-text-primary truncate">
                      {c.display_name || c.external_channel_id}
                    </div>
                    {c.display_name && (
                      <div className="text-[10px] text-text-tertiary truncate">
                        {c.external_channel_id}
                      </div>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => toggleActive.mutate(c)}
                    className={`shrink-0 rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                      c.is_active
                        ? 'bg-emerald-500/15 text-emerald-400'
                        : 'bg-stitch-outline-variant/15 text-text-tertiary'
                    }`}
                  >
                    {c.is_active ? 'active' : 'paused'}
                  </button>
                  <button
                    type="button"
                    onClick={() => remove.mutate(c.id)}
                    className="shrink-0 text-[10px] text-rose-400 hover:underline"
                  >
                    delete
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-text-tertiary italic">No channels linked yet.</p>
          )}
        </section>

        {/* New channel form */}
        <section>
          <h3 className="text-[10px] font-bold uppercase tracking-widest text-text-tertiary mb-2">
            Link a new channel
          </h3>
          <form onSubmit={handleCreate} className="flex flex-col gap-2">
            <label className="text-[10px] text-text-tertiary uppercase">
              Kind
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as 'slack')}
                className="w-full mt-1 px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm"
              >
                <option value="slack">Slack</option>
              </select>
            </label>
            <label className="text-[10px] text-text-tertiary uppercase">
              External channel id
              <input
                value={externalId}
                onChange={(e) => setExternalId(e.target.value)}
                placeholder="C0123456789"
                required
                className="w-full mt-1 px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary"
              />
            </label>
            <label className="text-[10px] text-text-tertiary uppercase">
              Display name (optional)
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="#general"
                className="w-full mt-1 px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary"
              />
            </label>
            <label className="text-[10px] text-text-tertiary uppercase">
              Bot token
              <input
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
                placeholder="xoxb-..."
                type="password"
                className="w-full mt-1 px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary"
              />
            </label>
            <button
              type="submit"
              disabled={create.isPending}
              className="self-end mt-2 rounded-md bg-stitch-primary px-4 py-1.5 text-xs font-bold text-stitch-on-primary-container disabled:opacity-50"
            >
              {create.isPending ? 'Linking…' : 'Link channel'}
            </button>
          </form>
        </section>
      </div>
    </Modal>
  )
}
