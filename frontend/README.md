# BSNexus Frontend

React + TypeScript frontend for BSNexus.

## Setup

```bash
cd frontend
pnpm install
```

## Development

```bash
pnpm dev      # Start dev server on port 3000
pnpm build    # Production build
pnpm lint     # ESLint check
```

## Tech Stack

- React 19 + TypeScript
- Vite (build tool)
- Tailwind CSS (styling)
- React Router v7 (routing)
- Zustand (state management)
- TanStack Query (server state)
- Axios (HTTP client)

## Pages

| Path | Page | Description |
|------|------|------------|
| `/` | Dashboard | Project list and overview |
| `/architect/:sessionId?` | Architect | LLM design chat with WebSocket streaming |
| `/board/:projectId` | Board | Real-time Kanban board |
| `/workers` | Workers | Worker status and management |

## Environment

Environment variables are managed in the root `.env` file (shared with backend). Copy from root:

```bash
cp ../.env.example ../.env
```

Vite loads `VITE_`-prefixed variables from the root `.env` via `envDir: '..'` in `vite.config.ts`.

| Variable | Description | Default |
|----------|-----------|---------|
| `VITE_API_URL` | Backend API URL | (empty, uses Vite proxy) |
