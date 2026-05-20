import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import type { PhyloEntry } from '../lib/types'

interface Props {
  phyloData: Record<string, PhyloEntry | null>
}

export function PhyloStratPanel({ phyloData }: Props) {
  const entries = Object.entries(phyloData)
    .filter(([, v]) => v?.taxon_name)
    .map(([, v]) => v!)

  if (entries.length < 2) return null

  // Group by phylostratum + taxon_name, count genes
  const counts: Record<string, { phylostratum: number; taxon_name: string; count: number }> = {}
  for (const e of entries) {
    const key = `${e.phylostratum}:${e.taxon_name}`
    if (!counts[key]) counts[key] = { phylostratum: e.phylostratum, taxon_name: e.taxon_name, count: 0 }
    counts[key].count++
  }

  const data = Object.values(counts)
    .sort((a, b) => b.phylostratum - a.phylostratum)
    .map((d) => ({ name: d.taxon_name, genes: d.count }))

  const chartHeight = Math.max(80, data.length * 30)

  return (
    <div style={{
      background: '#fafaf9', border: '1px solid var(--border)',
      borderRadius: '6px', padding: '16px 20px',
    }}>
      <div style={{
        fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
        textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '12px',
      }}>
        Phylostratigraphy
      </div>

      <ResponsiveContainer width="100%" height={chartHeight}>
        <BarChart data={data} layout="vertical" margin={{ left: 0, right: 20, top: 0, bottom: 0 }}>
          <XAxis type="number" tick={{ fontSize: 11 }} />
          <YAxis
            type="category" dataKey="name" width={200}
            tick={{ fontSize: 11 }} tickFormatter={(v: string) => v.length > 28 ? v.slice(0, 26) + '…' : v}
          />
          <Tooltip
            formatter={(value) => [`${value} gene${value !== 1 ? 's' : ''}`, 'Count']}
            contentStyle={{ fontSize: '12px' }}
          />
          <Bar dataKey="genes" fill="#6366f1" radius={[0, 3, 3, 0]} />
        </BarChart>
      </ResponsiveContainer>

      <p style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '10px' }}>
        Source: Liebeskind et al. 2016, <em>Genome Biology &amp; Evolution</em>.
        Phylostrata ordered oldest (top) to youngest (bottom).
      </p>
    </div>
  )
}
