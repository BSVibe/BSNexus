export default function TimelineView({ projectId: _projectId }: { projectId: string }) {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center">
        <span className="material-symbols-outlined text-4xl text-text-tertiary mb-3 block">timeline</span>
        <p className="text-sm text-text-secondary font-medium mb-1">Timeline View</p>
        <p className="text-xs text-text-tertiary">Gantt chart coming soon</p>
      </div>
    </div>
  )
}
