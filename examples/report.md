# Compute Probe Report

Measured on 2026-06-18 with:

```powershell
$env:PYTHONPATH='backend'; python -u scripts\probe_compute.py
```

Probe genes: SYP, SYNGAP1, CAMK2A, DLG4, MFSD2A, SLC2A1, SPOCK1.

Updated after the gnomAD GraphQL schema fix in `backend/nullifier/tools/gnomad.py`.

## Findings

The prior 1,750-gene estimate of 25-45 minutes cold was too optimistic for the measured live Ensembl path. The warm estimate changed materially after fixing gnomAD because successful constraint calls now cache.

Corrected estimate, using the original successful Ensembl/dN/dS baseline plus post-fix gnomAD timings:

| Mode | Total | Ensembl | gnomAD + phylo | dN/dS |
| --- | ---: | ---: | ---: | ---: |
| Cold | 192.22 min | 180.58 min | 11.56 min | 4.65 s |
| Warm | 25.80 s | 15.29 s | 5.86 s | 4.65 s |

Delta vs prior structural estimate:

| Mode | Prior | Measured | Delta |
| --- | ---: | ---: | ---: |
| Cold | 25-45 min | 192.22 min | +147.22 to +167.22 min |
| Warm | 5-10 min | 25.80 s | -4.57 to -9.57 min |

Post-fix live check: all seven `gnomad.fetch_constraint(ensg)` calls returned constraint dictionaries with LOEUF values. A second cached check returned all seven from SQLite with a 0.22 ms mean gnomAD latency.

Note: the post-fix full probe had transient Ensembl homology timeouts for CAMK2A and DLG4, so this corrected estimate does not replace the earlier clean Ensembl and dN/dS baseline. It updates only the gnomAD/phylo portion.

## Ensembl Endpoint Timing

| Endpoint | Cold mean | Warm mean |
| --- | ---: | ---: |
| `lookup_gene` | 1.21 s | 2.5 ms |
| `get_orthologs` | 3.76 s | 3.8 ms |
| `get_paralogs` | 1.43 s | 2.3 ms |

Cold Ensembl latency is response-time bound, not rate-limit bound. The 14 req/s cap is not the bottleneck for these full homology calls; `get_orthologs` averaged 3.76 s per gene.

## Ortholog Yield

| Gene | Ensembl ID | Orthologs | One-to-one | Paralogs |
| --- | --- | ---: | ---: | ---: |
| SYP | ENSG00000102003 | 91 | 91 | 3 |
| SYNGAP1 | ENSG00000197283 | 93 | 91 | 10 |
| CAMK2A | ENSG00000070808 | 84 | 84 | 22 |
| DLG4 | ENSG00000132535 | 91 | 89 | 3 |
| MFSD2A | ENSG00000168389 | 98 | 94 | 2 |
| SLC2A1 | ENSG00000117394 | 95 | 92 | 13 |
| SPOCK1 | ENSG00000152377 | 97 | 95 | 3 |

Totals: 649 orthologs, 636 one-to-one orthologs, 56 paralogs.

## gnomAD And Phylo

| Component | Mean | Result |
| --- | ---: | --- |
| `gnomad.fetch_constraint(ensg)` cold/network | 391.5 ms | All 7 returned constraint data. |
| `gnomad.fetch_constraint(ensg)` warm/cache | 0.22 ms | All 7 returned cached constraint data. |
| `phylo.lookup_phylo_age(sym)` cold/probe | 4.8 ms | Local TSV lookup; first load cost only. |
| `phylo.lookup_phylo_age(sym)` warm/cache check | 3.13 ms | Local lookup after prior load. |

The failed pre-fix behavior was caused by stale GraphQL field names: `loeuf` and `pLI`. The live schema uses `oe_lof_upper` and `pli`. The client now maps those raw schema fields back to the stable output keys `loeuf` and `pli`, so downstream consumers do not need changes.

## dN/dS Compute

`analyst._fetch_rdnds_data(gene_data, genes, use_cache=True)` completed in 4.65 s with warm CDS/protein caches.

| Metric | Count |
| --- | ---: |
| One-to-one orthologs considered | 636 |
| Codon pairs aligned | 571 |
| NG86 pairs scored | 569 |
| Species dN/dS values returned | 569 |

This is small relative to cold Ensembl I/O. For these seven starter genes, NG86 CPU work is not the bottleneck once CDS/protein cache entries are warm.

## Extrapolation Model

The report applies the requested model:

- Pre-filter: `lookup_gene` x 1,750.
- Light fetch: `lookup_gene + get_orthologs` x 1,743 non-starters.
- Full fetch: `lookup_gene + get_orthologs + get_paralogs + one extra Ensembl request proxy` x 7 starters.
- gnomAD + phylo: all 1,750 genes.
- dN/dS: observed seven-starter wall time.

The fourth full-fetch Ensembl request is proxied by the mean of the three measured Ensembl endpoints because this probe measured only lookup, orthologs, and paralogs.

## Recommendation

1. Keep the gnomAD schema fixture aligned with the live raw response fields (`oe_lof_upper`, `pli`) so tests guard the query-to-output mapping.
2. Treat cold large-set Ensembl homology fetches as multi-hour unless batching or a cheaper homology endpoint is used for non-starter screening.
3. Keep NG86 out of the critical-path concern for this starter-sized workload; the measured warm CPU pass was 4.65 s for 569 scored pairs.
