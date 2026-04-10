# BSNexus Frontend

React + TypeScript frontend for BSNexus. See the root
[CLAUDE.md](../CLAUDE.md) for the high-level architecture; this README
covers frontend-specific commands, routes, and module pointers.

## Setup

```bash
cd frontend
pnpm install
```

## Development

```bash
pnpm dev          # Dev server on port 3000
pnpm exec tsc -b  # Type-check (no emit)
pnpm lint         # ESLint
pnpm build        # Production build
pnpm test:e2e     # Playwright (mock-API mode by default)
```

## Tech Stack

- React 19 + TypeScript
- Vite (build tool)
- Tailwind CSS (styling)
- React Router v7 (routing)
- Zustand (UI state)
- TanStack Query (server state + cache)
- Axios (HTTP client)
- Server-Sent Events (`EventSource`) for live updates

## Pages

| Path                          | Page          | Description                                              |
| ----------------------------- | ------------- | -------------------------------------------------------- |
| `/`                           | Landing       | Marketing landing                                        |
| `/dashboard`                  | Dashboard     | Tenant project list, create modal                        |
| `/projects/:projectId?`       | Project       | Plan / Files / Timeline / Design / Agents tabs + chat    |
| `/agents`                     | Agents        | Org chart, hire flow, template apply (incl. specialists) |
| `/budget`                     | Budget        | Per-agent spend + monthly resets                         |
| `/settings`                   | Settings      | Global LLM, executor configs, install token              |

## Project Page tabs

- **Plan** — `PlanView` (`AgentStatusBar` top, `PlanTree` left,
  `DetailPanel` right). Subscribes to `/plan-tree/events` SSE for
  live task transitions.
- **Files** — `FileBrowser` over the workspace.
- **Timeline** — historical task timeline (placeholder for now).
- **Design** — `DesignView` reads/writes `<workspace>/design/system.bsd`
  and `<workspace>/design/screens/*.bsd`. The Designer agent edits the
  same files via the workspace tool, so the UI and the agent stay in
  sync.
- **Agents** — per-project agent presence. Hire flow lives at
  `/agents`.

The header carries two icons: a forum icon that opens
`ProjectChannelsModal` (Slack/Discord linking) and a chat panel toggle.

## Key Modules

- **`api/planTree.ts`** — `/plan-tree`, `/agent-status`, `/activity` clients
- **`api/design.ts`** — `.bsd` file CRUD client
- **`api/channels.ts`** — `ProjectChannel` CRUD client
- **`stores/planStore.ts`** — Zustand UI state for the Plan tree
- **`hooks/usePlanEvents.ts`** — SSE → React Query cache patcher
- **`hooks/useChatEvents.ts`** — Chat SSE → React Query cache patcher
- **`components/plan/`** — `PlanView`, `PlanTree`, `AgentStatusBar`, `DetailPanel`
- **`components/project/DesignView.tsx`** — `.bsd` file lister + spec viewer
- **`components/project/ProjectChannelsModal.tsx`** — Slack channel linking
- **`pages/AgentsPage.tsx`** — Org chart + always-on template picker

## Environment

Environment variables are managed in the root `.env` file (shared with
backend). Copy from root:

```bash
cp ../.env.example ../.env
```

Vite loads `VITE_`-prefixed variables from the root `.env` via
`envDir: '..'` in `vite.config.ts`.

| Variable        | Description       | Default                       |
| --------------- | ----------------- | ----------------------------- |
| `VITE_API_URL`  | Backend API URL   | empty (uses Vite proxy in dev)|

## E2E tests

Playwright specs live in `frontend/e2e/specs/`. The default mode mocks
every backend response via `frontend/e2e/helpers/mock-api.ts` so you
can run them without a live API or DB:

```bash
pnpm test:e2e
pnpm test:e2e:ui    # Playwright inspector
pnpm test:e2e:debug
```

`*-live.spec.ts` files run against a real backend (see
`helpers/live-setup.ts`).
