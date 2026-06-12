# V-Genes Stage 5 Benchmark Sources

This benchmark is the first executable Stage 5 gate. The CI fixture uses
source-pinned cached diagnostics so the validation runner is deterministic.
Exact gene/site extraction from the papers and supplements should be added as a
new benchmark version rather than mutating these records in place.

## Negative artifact families

- `NEG-ALIGN-DROSOPHILA-2011`: Markova-Raina & Petrov, Genome Research, 2011,
  DOI `10.1101/gr.115949.110`.
- `NEG-ALIGN-SITEWISE-2011`: Jordan & Goldman, Molecular Biology and Evolution,
  2011/2012 issue, DOI `10.1093/molbev/msr272`.
- `NEG-GBGC-HAR-2010`: Katzman et al., PLoS Genetics, 2010,
  DOI `10.1371/journal.pgen.1000960`.
- `NEG-GBGC-GENOME-2010`: Ratnakumar et al., Philosophical Transactions of the
  Royal Society B, 2010, DOI `10.1098/rstb.2010.0007`.

## Positive reproducible case

- `POS-ERC-SLC30A9-2021`: "Evolutionary rate covariation identifies SLC30A9
  (ZnT9) as a mitochondrial zinc transporter", Biochemical Journal, 2021,
  DOI `10.1042/bcj20210342`.

The positive fixture is held out from FP-risk weight calibration and passes
through `mirrortree_lite`, the same primary comparative path used by the
pipeline.
