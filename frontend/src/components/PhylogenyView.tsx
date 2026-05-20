import { useMemo, useState } from 'react'
import { hierarchy, cluster } from 'd3-hierarchy'
import type { ComputeTest, PamlGeneResult } from '../lib/types'

// ── Newick parser ─────────────────────────────────────────────────────────────

interface TreeNode {
  name: string
  length: number
  children: TreeNode[]
}

function parseNewick(s: string): TreeNode | null {
  try {
    let pos = 0

    function parseNode(): TreeNode {
      const node: TreeNode = { name: '', length: 0, children: [] }

      if (s[pos] === '(') {
        pos++ // consume '('
        node.children.push(parseNode())
        while (s[pos] === ',') {
          pos++
          node.children.push(parseNode())
        }
        if (s[pos] === ')') pos++ // consume ')'
      }

      // Read name (may include underscores, letters, digits)
      const nameStart = pos
      while (pos < s.length && !/[,):;]/.test(s[pos])) pos++
      node.name = s.slice(nameStart, pos).trim()

      // Strip PAML foreground label (#1) from display name
      node.name = node.name.replace(/\s*#\d+$/, '')

      // Read branch length
      if (s[pos] === ':') {
        pos++
        const lenStart = pos
        while (pos < s.length && !/[,);]/.test(s[pos])) pos++
        node.length = parseFloat(s.slice(lenStart, pos)) || 0
      }

      return node
    }

    const root = parseNode()
    return root
  } catch {
    return null
  }
}

// ── Layout ────────────────────────────────────────────────────────────────────

const W = 480
const H_PER_LEAF = 22
const LABEL_PAD = 8
const LEFT_PAD = 20

function omegaColor(omega: number): string {
  if (omega > 1.5) return '#c0392b'
  if (omega > 1.0) return '#e05c3a'
  if (omega < 0.5) return '#2980b9'
  if (omega < 1.0) return '#4e9af1'
  return '#6b7280'
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  pamlTest: ComputeTest
}

export function PhylogenyView({ pamlTest }: Props) {
  const [hoveredGene, setHoveredGene] = useState<string | null>(null)
  const [tooltipLeaf, setTooltipLeaf] = useState<{ x: number; y: number; text: string } | null>(null)

  // Pick gene with best (lowest) LRT p-value
  const bestGene: [string, PamlGeneResult] | null = useMemo(() => {
    const perGene = pamlTest.per_gene ?? {}
    const computed = Object.entries(perGene).filter(([, v]) => v.status === 'computed' && v.newick)
    if (!computed.length) return null
    return computed.reduce((best, cur) =>
      (cur[1].lrt_pvalue ?? 1) < (best[1].lrt_pvalue ?? 1) ? cur : best
    )
  }, [pamlTest.per_gene])

  const tree = useMemo(() => {
    if (!bestGene) return null
    return parseNewick(bestGene[1].newick!)
  }, [bestGene])

  if (!pamlTest.available || !bestGene || !tree) return null

  const [geneName, geneResult] = bestGene
  const foregroundSpecies = new Set(geneResult.foreground_species ?? [])

  // Count leaves to size the SVG
  let leafCount = 0
  const countLeaves = (n: TreeNode) => {
    if (!n.children.length) leafCount++
    else n.children.forEach(countLeaves)
  }
  countLeaves(tree)
  const svgH = Math.max(120, leafCount * H_PER_LEAF + 20)

  // d3-hierarchy layout
  const root = hierarchy(tree, (d) => d.children.length ? d.children : null)
  const layoutFn = cluster<TreeNode>().size([svgH - 20, W - LEFT_PAD - 160])
  layoutFn(root)

  // Collect edges and leaf positions
  const edges: { x1: number; y1: number; x2: number; y2: number; isForeground: boolean; omega: number | null }[] = []
  const labels: { x: number; y: number; name: string; isForeground: boolean; omega: number | null }[] = []

  root.each((node) => {
    const nx = (node as any).y + LEFT_PAD
    const ny = (node as any).x + 10

    if (node.parent) {
      const px = (node.parent as any).y + LEFT_PAD
      const py = (node.parent as any).x + 10
      const isFg = !node.children && foregroundSpecies.has(node.data.name)
      edges.push({ x1: px, y1: py, x2: nx, y2: ny, isForeground: isFg, omega: isFg ? (geneResult.omega_foreground ?? null) : null })
    }

    if (!node.children) {
      const isFg = foregroundSpecies.has(node.data.name)
      labels.push({
        x: nx, y: ny,
        name: node.data.name.replace(/_/g, ' '),
        isForeground: isFg,
        omega: isFg ? (geneResult.omega_foreground ?? null) : null,
      })
    }
  })

  const pFmt = (v: number) => v < 0.001 ? v.toExponential(2) : v.toFixed(4)

  return (
    <div style={{
      background: '#f8fafc', border: '1px solid var(--border)',
      borderRadius: '6px', padding: '16px 20px',
    }}>
      <div style={{
        fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
        textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '4px',
      }}>
        Branch-model ω — {geneName}
      </div>
      <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '12px' }}>
        Foreground: <strong>{pamlTest.foreground_group ?? geneResult.foreground_group}</strong>
        {' · '}2ΔlnL = {geneResult.lrt_chi2?.toFixed(3)}
        {' · '}p = {geneResult.lrt_pvalue !== null && geneResult.lrt_pvalue !== undefined ? pFmt(geneResult.lrt_pvalue) : '—'}
      </div>

      {/* Gene selector if multiple computed */}
      {Object.keys(pamlTest.per_gene ?? {}).filter(k => pamlTest.per_gene![k].status === 'computed').length > 1 && (
        <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '8px' }}>
          Showing best-LRT gene. Other computed genes: {
            Object.keys(pamlTest.per_gene ?? {})
              .filter(k => pamlTest.per_gene![k].status === 'computed' && k !== geneName)
              .join(', ')
          }
        </div>
      )}

      <svg
        width="100%" viewBox={`0 0 ${W} ${svgH}`}
        style={{ display: 'block', overflow: 'visible' }}
      >
        {/* Elbow connectors */}
        {edges.map((e, i) => {
          const color = e.isForeground && e.omega !== null
            ? omegaColor(e.omega)
            : '#9ca3af'
          const strokeW = e.isForeground ? 2.5 : 1.5
          return (
            <path
              key={i}
              d={`M${e.x1},${e.y1} H${e.x2} V${e.y2}`}
              fill="none" stroke={color} strokeWidth={strokeW}
            />
          )
        })}

        {/* Leaf labels */}
        {labels.map((l, i) => {
          const color = l.isForeground ? (l.omega !== null ? omegaColor(l.omega) : '#374151') : '#6b7280'
          const fw = l.isForeground ? 700 : 400
          return (
            <g key={i}
              onMouseEnter={(ev) => {
                if (l.omega !== null) {
                  const rect = (ev.currentTarget as SVGGElement).getBoundingClientRect()
                  const label = l.omega > 1 ? 'positive selection' : l.omega < 1 ? 'purifying/neutral' : 'neutral'
                  setTooltipLeaf({ x: rect.right + 4, y: rect.top, text: `ω = ${l.omega.toFixed(3)} (${label})` })
                }
                setHoveredGene(l.name)
              }}
              onMouseLeave={() => { setTooltipLeaf(null); setHoveredGene(null) }}
              style={{ cursor: l.omega !== null ? 'default' : 'default' }}
            >
              <circle cx={l.x} cy={l.y} r={3} fill={color} />
              <text
                x={l.x + LABEL_PAD} y={l.y}
                fontSize={11} fontWeight={fw} fill={color}
                dominantBaseline="middle"
                fontStyle="italic"
              >
                {l.name}
                {l.isForeground && l.omega !== null && (
                  <tspan fontSize={9} fontStyle="normal" fill={color} dx={4}>
                    ω={l.omega.toFixed(2)}
                  </tspan>
                )}
              </text>
            </g>
          )
        })}
      </svg>

      {/* Floating tooltip */}
      {tooltipLeaf && (
        <div style={{
          position: 'fixed', left: tooltipLeaf.x, top: tooltipLeaf.y,
          background: '#1e293b', color: '#f8fafc',
          fontSize: '11px', padding: '4px 8px', borderRadius: '4px',
          pointerEvents: 'none', zIndex: 1000, whiteSpace: 'nowrap',
          transform: 'translateY(-50%)',
        }}>
          {tooltipLeaf.text}
        </div>
      )}

      {/* Legend */}
      <div style={{ display: 'flex', gap: '16px', marginTop: '10px', fontSize: '10px', color: 'var(--text-muted)' }}>
        <span><span style={{ color: '#c0392b', fontWeight: 700 }}>■</span> ω ≫ 1 (positive selection)</span>
        <span><span style={{ color: '#4e9af1', fontWeight: 700 }}>■</span> ω &lt; 1 (purifying/neutral)</span>
        <span><span style={{ color: '#9ca3af', fontWeight: 700 }}>■</span> background</span>
      </div>

      <p style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '6px' }}>
        Branch model 2 LRT · PAML codeml · {geneResult.n_species} species
      </p>

      {/* Suppress unused state warning */}
      {hoveredGene && <></>}
    </div>
  )
}
