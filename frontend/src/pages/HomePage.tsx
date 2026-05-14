import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createRun } from '../lib/api'

export function HomePage() {
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [maxPapers, setMaxPapers] = useState(12)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (text.trim().length < 50) {
      setError('Input must be at least 50 characters.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const { run_id } = await createRun(text.trim(), maxPapers)
      navigate(`/runs/${run_id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start run')
      setLoading(false)
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg)' }}>

      {/* Dark header */}
      <header style={{
        background: '#0f172a',
        borderBottom: '1px solid #1e293b',
        padding: '14px 32px',
        display: 'flex',
        alignItems: 'baseline',
        justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px' }}>
          <span style={{
            fontFamily: 'ui-monospace, Consolas, monospace',
            fontSize: '13px',
            fontWeight: 700,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: '#f1f5f9',
          }}>
            Nullifier
          </span>
          <span style={{
            fontFamily: 'ui-monospace, Consolas, monospace',
            fontSize: '11px',
            color: '#475569',
            letterSpacing: '0.04em',
          }}>
            scientific hypothesis stress-tester
          </span>
        </div>
        <a
          href="/history"
          style={{
            fontFamily: 'ui-monospace, Consolas, monospace',
            fontSize: '11px',
            color: '#475569',
            textDecoration: 'none',
            letterSpacing: '0.04em',
          }}
          onMouseOver={(e) => (e.currentTarget.style.color = '#94a3b8')}
          onMouseOut={(e) => (e.currentTarget.style.color = '#475569')}
        >
          history →
        </a>
      </header>

      {/* Main content */}
      <main style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '48px 24px',
      }}>
        <div style={{ width: '100%', maxWidth: '680px' }}>

          <div style={{ marginBottom: '28px' }}>
            <h1 style={{
              margin: '0 0 6px',
              fontSize: '20px',
              fontWeight: 700,
              color: 'var(--text-heading)',
              letterSpacing: '-0.01em',
            }}>
              Stress-test a hypothesis
            </h1>
            <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-muted)' }}>
              Paste a hypothesis, abstract, or research proposal. The pipeline extracts falsifiable claims,
              retrieves evidence from four literature databases, and returns a structured verdict.
            </p>
          </div>

          <form onSubmit={handleSubmit}>
            <div style={{
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: '6px',
              overflow: 'hidden',
            }}>

              {/* Textarea */}
              <textarea
                style={{
                  display: 'block',
                  width: '100%',
                  height: '200px',
                  padding: '16px',
                  fontFamily: 'ui-monospace, Consolas, monospace',
                  fontSize: '13px',
                  lineHeight: '1.6',
                  color: 'var(--text-heading)',
                  background: 'transparent',
                  border: 'none',
                  outline: 'none',
                  resize: 'none',
                  boxSizing: 'border-box',
                }}
                placeholder="Paste hypothesis or research text here (minimum 50 characters)..."
                value={text}
                onChange={(e) => setText(e.target.value)}
              />

              {/* Toolbar */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '10px 16px',
                borderTop: '1px solid var(--border-light)',
                background: '#fafafa',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    Max papers / claim
                  </span>
                  <input
                    type="range"
                    min={4}
                    max={20}
                    value={maxPapers}
                    onChange={(e) => setMaxPapers(+e.target.value)}
                    style={{ width: '100px', accentColor: 'var(--oxford)' }}
                  />
                  <span style={{
                    fontFamily: 'ui-monospace, Consolas, monospace',
                    fontSize: '12px',
                    color: 'var(--text-heading)',
                    fontWeight: 600,
                    minWidth: '20px',
                  }}>
                    {maxPapers}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{
                    fontFamily: 'ui-monospace, Consolas, monospace',
                    fontSize: '11px',
                    color: 'var(--text-xs)',
                  }}>
                    {text.length} chars
                  </span>
                  <button
                    type="submit"
                    disabled={loading}
                    style={{
                      background: loading ? '#93c5fd' : 'var(--oxford)',
                      color: '#ffffff',
                      border: 'none',
                      borderRadius: '4px',
                      padding: '7px 18px',
                      fontSize: '13px',
                      fontWeight: 600,
                      cursor: loading ? 'default' : 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      transition: 'background 0.15s',
                    }}
                    onMouseOver={(e) => { if (!loading) e.currentTarget.style.background = 'var(--oxford-hover)' }}
                    onMouseOut={(e) => { if (!loading) e.currentTarget.style.background = 'var(--oxford)' }}
                  >
                    {loading ? (
                      <>
                        <svg className="animate-spin" style={{ width: '13px', height: '13px' }} viewBox="0 0 24 24" fill="none">
                          <circle style={{ opacity: 0.25 }} cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path style={{ opacity: 0.75 }} fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                        </svg>
                        Starting…
                      </>
                    ) : 'Run analysis'}
                  </button>
                </div>
              </div>
            </div>

            {error && (
              <div style={{
                marginTop: '10px',
                padding: '8px 12px',
                background: '#fef2f2',
                border: '1px solid #fecaca',
                borderRadius: '4px',
                fontSize: '13px',
                color: '#7f1d1d',
              }}>
                {error}
              </div>
            )}
          </form>

        </div>
      </main>
    </div>
  )
}
