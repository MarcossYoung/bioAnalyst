export type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export type Verdict =
  | 'STRONG' | 'MODERATE' | 'WEAK' | 'FALSIFIED' | 'NOVEL-UNTESTED' | 'RESULTS-PROBLEMATIC'

export interface RunSummary {
  id: string
  status: RunStatus
  created_at: number
  completed_at: number | null
  max_papers: number
  verdict?: string
  mode?: 'v4' | 'v5'
}

export interface AtomicClaim {
  id: string
  statement: string
  null_hypothesis: string
  // ...other fields exist but only id/statement are used by the UI
}

export interface NormalizedPaper {
  source: string
  id: string
  doi?: string | null
  title: string
  abstract?: string
  year?: number | null
  venue?: string | null
  citation_count?: number | null
  influential_citation_count?: number | null
  authors?: string[]
}

export interface PaperClassification {
  paper_id: string
  paper_title: string
  year?: number | null
  venue?: string | null
  classification: string  // supports | contradicts | tangential | confounder
  justification_quote: string
  reasoning: string
}

export interface FailedClassification {
  paper_id: string
  paper_title: string
  error: string
  drop_reason?: string
}

export interface ClassificationSummary {
  retrieved: number
  classified: number
  dropped: number
  drop_reasons: Record<string, number>
  classifier_degraded: boolean
}

export interface ClaimEvidence {
  claim_id: string
  evidence_strength: string  // strong | moderate | weak | absent
  novelty_flag: string       // well-studied | sparsely-studied | unstudied
  synthesis: string
  literature_gap?: string
  confounders_identified?: string | { confounder?: string }[]
  classifications: PaperClassification[]
  failed_classifications?: FailedClassification[]
  classification_summary?: ClassificationSummary
  classifier_degraded?: boolean
  retrieved_papers: NormalizedPaper[]
  queries_used?: { query: string; intent?: string }[]
}

export interface Evidence {
  cited_literature_validated: unknown[]
  claim_evidence: Record<string, ClaimEvidence>
  classifier_degraded?: boolean
  classification_summaries?: Record<string, ClassificationSummary>
  api_status: Record<string, string[]>
  flags_applied: number
}

// ── v5: completed-analysis critique ─────────────────────────────────────────

export interface CompletedFinding {
  finding: string
  statistic?: string
  test?: string
  sample_size?: string
  interpretation?: string
}

export interface Critique {
  severity: 'high' | 'medium' | 'low' | string
  issues: string[]
  notes?: string
}

export interface ReproCheckItem {
  reported: string
  ensembl_value: string
  verifiable: boolean
  note: string
}

// ── Skeptic output ──────────────────────────────────────────────────────────

export interface AltExplanation {
  explanation: string
  plausibility: string  // high | medium | low
  why?: string
  how_to_rule_out?: string
}

export interface Scores {
  statistical_robustness?: number
  literature_consensus?: number
  mechanistic_plausibility?: number
  counter_explanation_risk?: number
  novelty_adjusted_confidence?: number
  genomic_evidence_alignment?: number
  overall_falsifiability_score?: number
  // v5 conditional critique scores
  methods_critique_score?: number
  statistical_critique_score?: number
  reproducibility_score?: number
  interpretation_critique_score?: number
}

export interface VerdictData {
  verdict: Verdict
  scores: Scores
  verdict_justification?: string
  decisive_experiment?: string
  librarian_sanity_check?: string
  top_alternative_explanations?: AltExplanation[]
  // v5 conditional critique sub-objects
  methods_critique?: Critique
  statistical_critique?: Critique
  reproducibility_check?: Critique
  interpretation_critique?: Critique
}

// ── Analyst output ──────────────────────────────────────────────────────────

export interface AnalystSetStats {
  valid_gene_count: number
  missing_genes: string[]
  mean_ortholog_count: number
  mean_paralog_count: number
  mean_duplication_count: number
  dnds_n: number
  dnds_mean: number | null
  dnds_stdev: number | null
  dnds_max: number | null
  dnds_saturation_fraction?: number
  dnds_saturation_flag?: boolean
  omega_foreground_n?: number
  omega_foreground_mean?: number | null
  acceleration_ratio_n?: number
  acceleration_ratio_mean?: number | null
  foreground_label?: string | null
  dnds_diagnostics?: {
    genes_with_orthologs: number
    genes_with_dnds: number
    orthologs_total: number
    orthologs_with_dnds: number
    orthologs_missing_dn: number
    orthologs_missing_ds: number
    orthologs_invalid_ds: number
    orthologs_filtered_high: number
    dnds_source_counts?: Record<string, number>
  }
}

export interface AnalystCrossSet {
  set_a_tf_count: number
  set_b_tf_count: number
  shared_tfs: string[]
  jaccard_index: number
}

export interface AnalystPattern {
  pattern: string
  supports_hypothesis: string  // "yes" | "no" | "neutral"
  evidence: string
}

export interface AnalystOutlier {
  gene: string
  why_notable: string
  implication: string
}

export interface AnalystInterpretation {
  patterns_observed: AnalystPattern[]
  outlier_genes: AnalystOutlier[]
  regulatory_overlap?: {
    shared_tf_motifs: string[]
    jaccard_index: number
    interpretation: string
  }
  reproducibility_check?: ReproCheckItem[]
  limitations: string[]
  overall_genomic_assessment: string  // "supports" | "neutral" | "contradicts" | "inconclusive"
  assessment_justification: string
}

export interface AnalystReproducibility {
  reported_findings: CompletedFinding[]
  ensembl_retrievable: Record<string, Record<string, unknown>>
  not_verifiable_here: string[]
  verifiable_count: number
  total: number
}

export interface AnalystResult {
  skipped: boolean
  reason?: string
  set_a: string[]
  set_b: string[]
  gene_data?: Record<string, unknown>
  set_a_stats: AnalystSetStats | null
  set_b_stats: AnalystSetStats | null
  cross_set: AnalystCrossSet | null
  reproducibility?: AnalystReproducibility | null
  interpretation: AnalystInterpretation
  // v6 fields
  expansion?: GeneSetExpansion | null
  compute_results?: ComputeResult | null
  robustness?: RobustnessResult | null
  // v7 fields
  phylo_data?: Record<string, PhyloEntry | null>
  data_provenance?: DataProvenance | null
  dnds_saturation?: {
    flag: boolean
    max_fraction: number
    threshold: number
    reason: string
  } | null
}

// ── v6: gene-set expansion ───────────────────────────────────────────────────

export interface GeneSetExpansion {
  syngo_release: string | null
  bbb_version: string | null
  starter_count: number
  expanded_sets: string[]
  control_sets: string[]
  total_expanded: number
  total_controls: number
  provenance?: Record<string, unknown> | null
}

// ── v6: compute layer ────────────────────────────────────────────────────────

export interface PamlGeneResult {
  status: string
  gene?: string
  omega_foreground?: number | null
  omega_background?: number | null
  acceleration_ratio?: number | null
  lrt_chi2?: number | null
  lrt_statistic?: number | null
  lrt_pvalue?: number | null
  alignment_length?: number
  species_count?: number
  n_species?: number
  foreground_species?: string[]
  foreground_group?: string
  newick?: string | null
}

export interface PhyloEntry {
  phylostratum: number
  taxon_name: string
  _source: string
  _version: string
}

export interface DataProvenance {
  gnomad?: { source: string; genome_build: string; genes_with_loeuf: number; total_genes: number } | null
  phylo?: { source: string; version: string; genes_with_age: number; total_genes: number } | null
  compara?: {
    source: string
    genes_with_orthologs: number
    genes_via_ensg_fallback?: number
    genes_not_in_compara?: number
    dnds_source_counts?: Record<string, number>
    total_genes: number
  } | null
  paml?: { source: string; genes_computed: number; total_genes: number } | null
}

export interface ComputeTest {
  test: string
  p_value: number | null
  significant: boolean | null
  significant_adjusted?: boolean | null
  effect_size?: number | null
  effect_size_name?: string | null
  effect_size_label?: string | null
  ci_lower?: number | null
  ci_upper?: number | null
  n?: number | null
  error?: string | null
  skipped?: boolean
  skip_reason?: string
  details?: Record<string, unknown>
  closest_alternative?: string
  // v7: paml_branch_model fields
  available?: boolean
  per_gene?: Record<string, PamlGeneResult>
  foreground_group?: string
  method?: string
}

export interface ComputeResult {
  tests: ComputeTest[]
  corrections_applied: Array<string | Record<string, unknown>>
  untestable?: boolean
  untestable_reason?: string
  required_construct?: string
}

// ── v6: robustness ───────────────────────────────────────────────────────────

export interface RobustnessResult {
  stability: 'stable' | 'sensitive' | 'fragile' | 'unknown' | string
  agreement_fraction: number
  most_influential_genes: string[]
  applicable?: boolean
  reason?: string
  status?: 'ran' | 'skipped' | 'not_applicable' | string
}

// ── Confirmation gate (section-based) ───────────────────────────────────────

export type SectionKind = 'text' | 'list' | 'findings'

export interface ConfirmSection {
  id: string
  label: string
  kind: SectionKind
  removable: boolean
  detected: boolean
  value: string | string[] | CompletedFinding[]
}

export interface ConfirmPayload {
  sections: ConfirmSection[]
  domain: string
}

export interface SectionEdit {
  action: 'keep' | 'edit' | 'remove'
  value?: string | string[] | CompletedFinding[]
}

// ── Flags ───────────────────────────────────────────────────────────────────

export interface Flag {
  id: number
  created_at: string
  hypothesis_summary: string
  domain: string | null
  entities_json: string | null
  paper_title: string
  paper_abstract_excerpt: string
  agent_classification: string
  agent_justification: string
  user_classification: string
  user_reason: string | null
}

// WebSocket event payloads
export interface WsEvent {
  seq: number
  type: string
  payload: Record<string, unknown>
  ts: number
}
