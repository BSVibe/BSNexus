/**
 * Minimal Lucide-style line icons used across BSNexus.
 * Mirror of the design bundle's `I.*` glyphs.
 */
import type { CSSProperties } from 'react'

interface IconProps {
  size?: number
  stroke?: number
  style?: CSSProperties
  className?: string
  title?: string
  children: React.ReactNode
}

function Icon({ size = 16, stroke = 1.75, style, className, title, children }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flex: 'none', ...style }}
      className={className}
      aria-label={title}
      role={title ? 'img' : undefined}
    >
      {children}
    </svg>
  )
}

type P = Omit<IconProps, 'children'>

export const I = {
  Home: (p: P) => (
    <Icon {...p}>
      <path d="M3 11l9-7 9 7" />
      <path d="M5 10v10h14V10" />
    </Icon>
  ),
  Chat: (p: P) => (
    <Icon {...p}>
      <path d="M21 12a8 8 0 0 1-11.5 7.2L4 21l1.7-5.3A8 8 0 1 1 21 12z" />
    </Icon>
  ),
  Timeline: (p: P) => (
    <Icon {...p}>
      <path d="M4 6h16M4 12h10M4 18h16" />
      <circle cx="3" cy="6" r="1.2" />
      <circle cx="3" cy="12" r="1.2" />
      <circle cx="3" cy="18" r="1.2" />
    </Icon>
  ),
  Inbox: (p: P) => (
    <Icon {...p}>
      <path d="M3 13l3-8h12l3 8M3 13v6h18v-6M3 13h5l2 3h4l2-3h5" />
    </Icon>
  ),
  Search: (p: P) => (
    <Icon {...p}>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" />
    </Icon>
  ),
  Plus: (p: P) => (
    <Icon {...p}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  ),
  Settings: (p: P) => (
    <Icon {...p}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14 3h-4l-.6 2.5a7 7 0 0 0-2 1.2L5 5.8l-2 3.4 2 1.6A7 7 0 0 0 5 12a7 7 0 0 0 .1 1.2l-2 1.6 2 3.4 2.3-.9a7 7 0 0 0 2 1.2L10 21h4l.6-2.5a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.6A7 7 0 0 0 19 12z" />
    </Icon>
  ),
  ChevRight: (p: P) => (
    <Icon {...p}>
      <path d="M9 6l6 6-6 6" />
    </Icon>
  ),
  ChevLeft: (p: P) => (
    <Icon {...p}>
      <path d="M15 6l-6 6 6 6" />
    </Icon>
  ),
  ChevDown: (p: P) => (
    <Icon {...p}>
      <path d="M6 9l6 6 6-6" />
    </Icon>
  ),
  X: (p: P) => (
    <Icon {...p}>
      <path d="M6 6l12 12M18 6L6 18" />
    </Icon>
  ),
  Send: (p: P) => (
    <Icon {...p}>
      <path d="M4 12l16-8-6 18-3-7-7-3z" />
    </Icon>
  ),
  Code: (p: P) => (
    <Icon {...p}>
      <path d="M8 6l-5 6 5 6M16 6l5 6-5 6M14 4l-4 16" />
    </Icon>
  ),
  Doc: (p: P) => (
    <Icon {...p}>
      <path d="M7 3h8l4 4v14H7z" />
      <path d="M15 3v5h4" />
    </Icon>
  ),
  Design: (p: P) => (
    <Icon {...p}>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 4v16M4 12h16" />
    </Icon>
  ),
  Data: (p: P) => (
    <Icon {...p}>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" />
    </Icon>
  ),
  Url: (p: P) => (
    <Icon {...p}>
      <path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1" />
      <path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1" />
    </Icon>
  ),
  Eye: (p: P) => (
    <Icon {...p}>
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
      <circle cx="12" cy="12" r="3" />
    </Icon>
  ),
  Command: (p: P) => (
    <Icon {...p}>
      <path d="M9 9V6a3 3 0 1 0-3 3h3zm0 0h6m-6 0v6m6-6V6a3 3 0 1 1 3 3h-3zm0 0v6m0 0h-6m6 0v3a3 3 0 1 0 3-3h-3zm-6 0v3a3 3 0 1 1-3-3h3z" />
    </Icon>
  ),
  Check: (p: P) => (
    <Icon {...p}>
      <path d="M5 12l5 5 10-11" />
    </Icon>
  ),
  Alert: (p: P) => (
    <Icon {...p}>
      <path d="M12 9v4m0 4h.01M10.3 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    </Icon>
  ),
  Copy: (p: P) => (
    <Icon {...p}>
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </Icon>
  ),
  GitBranch: (p: P) => (
    <Icon {...p}>
      <circle cx="6" cy="3" r="2" />
      <circle cx="6" cy="18" r="2" />
      <circle cx="18" cy="6" r="2" />
      <path d="M6 5v11M18 8c0 4-6 4-6 8" />
    </Icon>
  ),
  Zap: (p: P) => (
    <Icon {...p}>
      <path d="M13 2L3 14h8l-1 8 10-12h-8l1-8z" />
    </Icon>
  ),
  Brain: (p: P) => (
    <Icon {...p}>
      <path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 2 5v1a3 3 0 0 0 6 0V4a3 3 0 0 0-3 0z" />
      <path d="M15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-2 5v1a3 3 0 0 1-6 0" />
    </Icon>
  ),
  Shield: (p: P) => (
    <Icon {...p}>
      <path d="M12 2l8 4v6c0 5-3.5 9-8 10-4.5-1-8-5-8-10V6l8-4z" />
    </Icon>
  ),
  Gateway: (p: P) => (
    <Icon {...p}>
      <path d="M4 21V8l8-5 8 5v13M9 21v-6h6v6M2 21h20" />
    </Icon>
  ),
  Filter: (p: P) => (
    <Icon {...p}>
      <path d="M3 5h18l-7 8v6l-4 2v-8z" />
    </Icon>
  ),
  Book: (p: P) => (
    <Icon {...p}>
      <path d="M4 4a2 2 0 0 1 2-2h14v18H6a2 2 0 0 0-2 2V4z" />
      <path d="M4 18a2 2 0 0 0 2 2h14" />
    </Icon>
  ),
  Refresh: (p: P) => (
    <Icon {...p}>
      <path d="M21 4v6h-6M3 20v-6h6" />
      <path d="M20 9a8 8 0 0 0-14.9-2M4 15a8 8 0 0 0 14.9 2" />
    </Icon>
  ),
  Logout: (p: P) => (
    <Icon {...p}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
    </Icon>
  ),
  Sparkle: (p: P) => (
    <Icon {...p}>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8" />
    </Icon>
  ),
  Tree: (p: P) => (
    <Icon {...p}>
      <circle cx="12" cy="5" r="2" />
      <circle cx="6" cy="19" r="2" />
      <circle cx="18" cy="19" r="2" />
      <path d="M12 7v5M12 12H6v5M12 12h6v5" />
    </Icon>
  ),
}
