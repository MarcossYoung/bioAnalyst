# Stage-4 RERconverge driver contract.
#
# The Python side calls the Stage-0 genomics container with tool="rerconverge"
# and expects JSON with:
#   status, trait, set_results, control_results, primate_out_results,
#   tool_versions, provenance, error
#
# This file documents the intended in-container entry point. The repository
# snapshot used by tests does not execute R directly on the host.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("usage: rerconverge_driver.R <input.json> <output.json>", call. = FALSE)
}

if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("jsonlite is required inside the genomics container", call. = FALSE)
}
if (!requireNamespace("RERconverge", quietly = TRUE)) {
  stop("RERconverge is required inside the genomics container", call. = FALSE)
}

input_path <- args[[1]]
output_path <- args[[2]]
payload <- jsonlite::fromJSON(input_path, simplifyVector = FALSE)
or_default <- function(value, default) {
  if (is.null(value)) default else value
}

# Container implementation placeholder:
# 1. Convert payload$rate_vectors and payload$trait_axis into RERconverge inputs.
# 2. Run foreground-set and matched-control phenotype correlations.
# 3. Re-run the same tests with primate species removed.
# 4. Emit JSON conforming to backend.nullifier.tools.rerconverge.coerce_container_result.
result <- list(
  status = "unavailable",
  trait = or_default(payload$trait, "cortical_neurons"),
  set_results = list(),
  control_results = list(),
  primate_out_results = list(),
  error = "rerconverge_driver.R is a container contract stub in this checkout"
)

jsonlite::write_json(result, output_path, auto_unbox = TRUE, null = "null", pretty = TRUE)
