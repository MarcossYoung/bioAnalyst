import { useMemo, useState } from 'react'
import type { ConfirmPayload, ConfirmSection, SectionEdit, CompletedFinding } from '../lib/types'

interface ConfirmModalProps {
  payload: ConfirmPayload
  onSubmit: (edits: Record<string, SectionEdit>) => void
  onAbort: () => void
}

type Mode = 'keep' | 'edit' | 'remove'

interface CardState {
  mode: Mode
  textDraft: string
  listDraft: string                 // one item per line
  findingsDraft: CompletedFinding[]
}

const FINDING_FIELDS: [keyof CompletedFinding, string][] = [
  ['finding', 'Finding'],
  ['statistic', 'Statistic'],
  ['test', 'Test'],
  ['sample_size', 'Sample size'],
  ['interpretation', 'Interpretation'],
]

function initCard(s: ConfirmSection): CardState {
  return {
    mode: 'keep',
    textDraft: s.kind === 'text' ? String(s.value ?? '') : '',
    listDraft: s.kind === 'list' ? (s.value as string[]).join('\n') : '',
    findingsDraft: s.kind === 'findings' ? structuredClone(s.value as CompletedFinding[]) : [],
  }
}

const LABEL_STYLE: React.CSSProperties = {
  fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
  textTransform: 'uppercase', color: 'var(--text-muted)',
}

function btn(active: boolean, danger = false): React.CSSProperties {
  return {
    fontSize: '11px', padding: '2px 9px', borderRadius: '3px', cursor: 'pointer',
    border: '1px solid',
    borderColor: active ? (danger ? '#fecaca' : 'var(--oxford)') : 'var(--border)',
    background: active ? (danger ? '#fef2f2' : 'var(--oxford)') : 'var(--surface)',
    color: active ? (danger ? '#7f1d1d' : '#fff') : 'var(--text-body)',
    fontFamily: 'ui-monospace, Consolas, monospace',
  }
}

const inputStyle: React.CSSProperties = {
  width: '100%', fontSize: '13px', padding: '4px 6px', border: '1px solid var(--border)',
  borderRadius: '3px', fontFamily: 'inherit', boxSizing: 'border-box',
}

export function ConfirmModal({ payload, onSubmit, onAbort }: ConfirmModalProps) {
  const sections = payload.sections
  const [state, setState] = useState<Record<string, CardState>>(
    () => Object.fromEntries(sections.map((s) => [s.id, initCard(s)])),
  )

  function setMode(id: string, mode: Mode) {
    setState((prev) => ({ ...prev, [id]: { ...prev[id], mode } }))
  }
  function patch(id: string, p: Partial<CardState>) {
    setState((prev) => ({ ...prev, [id]: { ...prev[id], ...p } }))
  }

  const edits = useMemo<Record<string, SectionEdit>>(() => {
    const out: Record<string, SectionEdit> = {}
    for (const s of sections) {
      const cs = state[s.id]
      if (cs.mode === 'keep') continue
      if (cs.mode === 'remove') { out[s.id] = { action: 'remove' }; continue }
      if (s.kind === 'text') out[s.id] = { action: 'edit', value: cs.textDraft.trim() }
      else if (s.kind === 'list') out[s.id] = { action: 'edit', value: cs.listDraft.split('\n').map((x) => x.trim()).filter(Boolean) }
      else out[s.id] = { action: 'edit', value: cs.findingsDraft }
    }
    return out
  }, [sections, state])

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px', zIndex: 50,
    }}>
      <div style={{
        background: 'var(--surface)', borderRadius: '8px', width: '720px', maxWidth: '100%',
        maxHeight: '88vh', display: 'flex', flexDirection: 'column', overflow: 'hidden',
        boxShadow: '0 20px 50px rgba(0,0,0,0.3)',
      }}>
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border-light)' }}>
          <div style={LABEL_STYLE}>Review extracted structure — domain: {payload.domain}</div>
          <p style={{ margin: '6px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
            Keep, edit, or remove each section before the run proceeds. Only the hypothesis is required.
            Cards are focusable — press <code>k</code>/<code>e</code>/<code>r</code> on a focused card.
          </p>
        </div>

        <div style={{ overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {sections.map((s) => {
            const cs = state[s.id]
            return (
              <div
                key={s.id}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'k') setMode(s.id, 'keep')
                  if (e.key === 'e') setMode(s.id, 'edit')
                  if (e.key === 'r' && s.removable) setMode(s.id, 'remove')
                }}
                style={{
                  border: '1px solid var(--border)', borderRadius: '6px', padding: '10px 12px',
                  background: cs.mode === 'remove' ? '#fdf2f2' : 'var(--surface)', outline: 'none',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px', flexWrap: 'wrap' }}>
                  <span style={{ ...LABEL_STYLE, color: 'var(--text-heading)' }}>{s.label}</span>
                  {s.detected && (
                    <span style={{
                      fontSize: '10px', color: 'var(--verdict-moderate)', background: 'var(--verdict-moderate-bg)',
                      border: '1px solid var(--verdict-moderate-border)', padding: '0 5px', borderRadius: '3px',
                    }}>detected</span>
                  )}
                  <div style={{ flex: 1 }} />
                  <button style={btn(cs.mode === 'keep')} onClick={() => setMode(s.id, 'keep')}>keep</button>
                  <button style={btn(cs.mode === 'edit')} onClick={() => setMode(s.id, 'edit')}>edit</button>
                  {s.removable && (
                    <button style={btn(cs.mode === 'remove', true)} onClick={() => setMode(s.id, 'remove')}>remove</button>
                  )}
                </div>

                {cs.mode === 'remove' && (
                  <p style={{ margin: 0, fontSize: '12px', color: '#9a3412', fontStyle: 'italic' }}>
                    This section will be dropped before the run continues.
                  </p>
                )}

                {cs.mode === 'keep' && <SectionPreview section={s} />}

                {cs.mode === 'edit' && s.kind === 'text' && (
                  <textarea
                    value={cs.textDraft}
                    onChange={(e) => patch(s.id, { textDraft: e.target.value })}
                    rows={4}
                    style={{ ...inputStyle, fontFamily: 'Georgia, serif', resize: 'vertical' }}
                  />
                )}

                {cs.mode === 'edit' && s.kind === 'list' && (
                  <textarea
                    value={cs.listDraft}
                    onChange={(e) => patch(s.id, { listDraft: e.target.value })}
                    rows={Math.max(3, cs.listDraft.split('\n').length)}
                    placeholder="one item per line"
                    style={{ ...inputStyle, fontFamily: 'ui-monospace, Consolas, monospace', resize: 'vertical' }}
                  />
                )}

                {cs.mode === 'edit' && s.kind === 'findings' && (
                  <FindingsEditor
                    findings={cs.findingsDraft}
                    onChange={(f) => patch(s.id, { findingsDraft: f })}
                  />
                )}
              </div>
            )
          })}
        </div>

        <div style={{
          padding: '12px 20px', borderTop: '1px solid var(--border-light)',
          display: 'flex', justifyContent: 'flex-end', gap: '10px', alignItems: 'center',
        }}>
          <span style={{ marginRight: 'auto', fontSize: '11px', color: 'var(--text-xs)' }}>
            {Object.keys(edits).length === 0 ? 'No changes' : `${Object.keys(edits).length} section edit(s) pending`}
          </span>
          <button
            onClick={onAbort}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '12px', color: '#ef4444', padding: '6px 10px' }}
          >
            Abort run
          </button>
          <button
            onClick={() => onSubmit(edits)}
            style={{
              background: 'var(--oxford)', color: '#fff', border: 'none', borderRadius: '4px',
              cursor: 'pointer', fontSize: '13px', padding: '7px 16px', fontWeight: 600,
            }}
          >
            {Object.keys(edits).length === 0 ? 'Proceed' : 'Apply & proceed'}
          </button>
        </div>
      </div>
    </div>
  )
}

function SectionPreview({ section }: { section: ConfirmSection }) {
  if (section.kind === 'text') {
    return <p className="prose" style={{ margin: 0, fontSize: '14px', color: 'var(--text-heading)' }}>{String(section.value ?? '')}</p>
  }
  if (section.kind === 'list') {
    const items = section.value as string[]
    if (!items.length) return <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-xs)' }}>(none)</p>
    return (
      <ul style={{ margin: 0, paddingLeft: '16px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
        {items.map((x, i) => <li key={i} style={{ fontSize: '13px' }}>{x}</li>)}
      </ul>
    )
  }
  const findings = section.value as CompletedFinding[]
  if (!findings.length) return <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-xs)' }}>(none)</p>
  return (
    <ul style={{ margin: 0, paddingLeft: '16px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
      {findings.map((f, i) => (
        <li key={i} style={{ fontSize: '13px' }}>
          {f.finding}
          {(f.statistic || f.test || f.sample_size) && (
            <span style={{ fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '11px', color: 'var(--text-muted)' }}>
              {' '}— {[f.statistic, f.test, f.sample_size].filter(Boolean).join(' · ')}
            </span>
          )}
          {f.interpretation && <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontStyle: 'italic' }}>→ {f.interpretation}</div>}
        </li>
      ))}
    </ul>
  )
}

function FindingsEditor({ findings, onChange }: { findings: CompletedFinding[]; onChange: (f: CompletedFinding[]) => void }) {
  function set(i: number, key: keyof CompletedFinding, val: string) {
    const next = findings.slice()
    next[i] = { ...next[i], [key]: val }
    onChange(next)
  }
  function add() {
    onChange([...findings, { finding: '', statistic: '', test: '', sample_size: '', interpretation: '' }])
  }
  function remove(i: number) {
    onChange(findings.filter((_, idx) => idx !== i))
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {findings.map((f, i) => (
        <div key={i} style={{ border: '1px solid var(--border-light)', borderRadius: '4px', padding: '8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{ fontSize: '10px', color: 'var(--text-xs)', fontFamily: 'ui-monospace, Consolas, monospace' }}>#{i + 1}</span>
            <div style={{ flex: 1 }} />
            <button onClick={() => remove(i)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#ef4444', fontSize: '11px' }}>remove</button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {FINDING_FIELDS.map(([key, label]) => (
              <input
                key={key}
                value={(f[key] as string) ?? ''}
                onChange={(e) => set(i, key, e.target.value)}
                placeholder={label}
                style={inputStyle}
              />
            ))}
          </div>
        </div>
      ))}
      <button
        onClick={add}
        style={{
          alignSelf: 'flex-start', fontSize: '11px', padding: '3px 10px', borderRadius: '3px',
          border: '1px dashed var(--border)', background: 'var(--surface)', cursor: 'pointer',
          fontFamily: 'ui-monospace, Consolas, monospace',
        }}
      >
        + add finding
      </button>
    </div>
  )
}
