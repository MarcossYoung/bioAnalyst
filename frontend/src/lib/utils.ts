export function cn(...classes: (string | undefined | false | null)[]): string {
  return classes.filter(Boolean).join(' ')
}

export function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

export function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

export function formatDuration(start: number, end: number | null): string {
  if (!end) return '—'
  const s = Math.round(end - start)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export const VERDICT_STYLE: Record<string, { color: string; bg: string; border: string }> = {
  STRONG:         { color: 'var(--verdict-strong)',    bg: 'var(--verdict-strong-bg)',    border: 'var(--verdict-strong-border)' },
  MODERATE:       { color: 'var(--verdict-moderate)',  bg: 'var(--verdict-moderate-bg)',  border: 'var(--verdict-moderate-border)' },
  WEAK:           { color: 'var(--verdict-weak)',      bg: 'var(--verdict-weak-bg)',      border: 'var(--verdict-weak-border)' },
  FALSIFIED:      { color: 'var(--verdict-falsified)', bg: 'var(--verdict-falsified-bg)', border: 'var(--verdict-falsified-border)' },
  'NOVEL-UNTESTED': { color: 'var(--verdict-novel)',   bg: 'var(--verdict-novel-bg)',     border: 'var(--verdict-novel-border)' },
  'RESULTS-PROBLEMATIC': { color: 'var(--verdict-problematic)', bg: 'var(--verdict-problematic-bg)', border: 'var(--verdict-problematic-border)' },
}

export const SEVERITY_STYLE: Record<string, { color: string; bg: string; border: string }> = {
  high:   { color: 'var(--severity-high)',   bg: 'var(--severity-high-bg)',   border: 'var(--severity-high-border)' },
  medium: { color: 'var(--severity-medium)', bg: 'var(--severity-medium-bg)', border: 'var(--severity-medium-border)' },
  low:    { color: 'var(--severity-low)',    bg: 'var(--severity-low-bg)',    border: 'var(--severity-low-border)' },
}

export const CLASSIFICATION_OPTIONS = ['supports', 'contradicts', 'tangential', 'confounder'] as const

export function scoreColor(v: number): string {
  return v <= 3 ? '#9a3412' : v <= 6 ? '#92400e' : '#166534'
}

export const CLASS_STYLE: Record<string, string> = {
  supports:    'color: #166534; background: #f0fdf4; border-color: #bbf7d0',
  contradicts: 'color: #7f1d1d; background: #fef2f2; border-color: #fecaca',
  tangential:  'color: #374151; background: #f9fafb; border-color: #d1d5db',
  confounder:  'color: #92400e; background: #fffbeb; border-color: #fde68a',
}

export const STATUS_STYLE: Record<string, string> = {
  pending:   'color: #6b7280; background: #f9fafb; border-color: #d1d5db',
  running:   'color: #1e40af; background: #eff6ff; border-color: #bfdbfe',
  completed: 'color: #166534; background: #f0fdf4; border-color: #bbf7d0',
  failed:    'color: #7f1d1d; background: #fef2f2; border-color: #fecaca',
  cancelled: 'color: #6b7280; background: #f9fafb; border-color: #d1d5db',
}

export const STRENGTH_COLOR: Record<string, string> = {
  strong:   '#166534',
  moderate: '#92400e',
  weak:     '#9a3412',
  absent:   '#7f1d1d',
}
