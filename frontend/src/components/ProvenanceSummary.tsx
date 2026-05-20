import type { DataProvenance } from '../lib/types'

interface Props {
  provenance: DataProvenance | null | undefined
}

export function ProvenanceSummary({ provenance }: Props) {
  if (!provenance) return null

  const chips: string[] = []
  if (provenance.gnomad) {
    chips.push(`gnomAD ${provenance.gnomad.genome_build} · ${provenance.gnomad.genes_with_loeuf} genes`)
  }
  if (provenance.compara) {
    chips.push(`Ensembl Compara · ${provenance.compara.genes_with_orthologs} genes`)
  }
  if (provenance.phylo) {
    chips.push(`Liebeskind 2016 · ${provenance.phylo.genes_with_age} genes`)
  }

  if (chips.length === 0) return null

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '10px' }}>
      {chips.map((chip) => (
        <span
          key={chip}
          style={{
            fontSize: '10px', fontWeight: 500, letterSpacing: '0.03em',
            padding: '2px 8px', borderRadius: '10px',
            background: '#f1f5f9', border: '1px solid #e2e8f0',
            color: '#64748b',
          }}
        >
          {chip}
        </span>
      ))}
    </div>
  )
}
