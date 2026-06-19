# Librarian — Semantic Scholar Graph API refactor

## Implementation status

Implemented in June 2026:

- `/paper/search` requests enriched fields and applies configurable field-of-study and minimum-citation filters.
- `/paper/search/match` is the primary cited-title lookup, with federated fallback and shared host-breaker handling.
- `/snippet/search` is available as an opt-in evidence source and shares the Semantic Scholar circuit breaker and per-claim deadline.
- The librarian hunt loop has explicit routing, configuration defaults, events, and frontend timeline output.
- Abstract quotations and full-text snippet quotations remain separate (`justification_quote` and `snippet_quote`) so provenance is unambiguous.

Default snippet search remains disabled. Enable it with `use_snippet_search = true` under `[literature]` after configuring `SEMANTIC_SCHOLAR_API_KEY` for practical rate limits.

Adopt better-fitted Academic Graph API calls for the Librarian's literature
reading. (The Datasets API was evaluated and rejected — it is a bulk
corpus-snapshot download, hundreds of GB–TB with no per-query interface, a
different subsystem entirely. `/paper/search/bulk` is also rejected: no
relevance ranking, wrong for per-claim retrieval.)

Previous state: the project used exactly one Graph endpoint — `GET /paper/search`
— minimally: only
`query/limit/fields`, no filters, and `FIELDS` omits several useful signals.

## Implemented design

### Phase 1 — Enrich `/paper/search` fields + bio-domain filtering (low risk, high value)

In `tools/sources/semantic_scholar.py`:

1. Extend `FIELDS` to add `tldr`, `openAccessPdf`, `publicationTypes`,
   `publicationDate`, `s2FieldsOfStudy`. `tldr` is the headline win — a
   SciTLDR one-line summary the classifier can read when the abstract is long
   or borderline.
2. Add optional search filters to the `params` dict, driven by config:
   - `fieldsOfStudy` (e.g. `"Biology,Medicine"`) — biases retrieval to the
     domain this tool actually analyses.
   - `minCitationCount` (optional, default unset) — drop noise.
   These are query-string params on the same `GET /paper/search` call; no new
   endpoint, no new failure mode for the `SourceHealth` breaker.
3. Normalize the new fields into the paper dict (alongside existing
   `source/id/doi/title/abstract/...`): add `tldr`, `open_access_pdf`,
   `publication_types`, `fields_of_study`.

In `tools/literature.py`:
4. Use the new signals in `_paper_score()` — small bonus for `tldr` presence
   and for biology/medicine `s2FieldsOfStudy`; this is optional polish.

In `agents/librarian.py`:
5. Where the per-paper classification prompt is assembled, append the `tldr`
   (when present) under the abstract so the classifier has a distilled summary.
   The verbatim-quote requirement stays anchored to the **abstract** to preserve
   the existing provenance guarantee.

In `config/default_config.toml` `[literature]`:
6. Add `semantic_scholar_fields_of_study = "Biology,Medicine"` and
   `semantic_scholar_min_citations = 0` (0 = unset). Read via the existing
   loader overlay so users need no config changes.

Representative file: `backend/nullifier/tools/sources/semantic_scholar.py`
(only Semantic Scholar gains these; the other source modules are untouched).

### Phase 2 — Use `/paper/search/match` for cited-literature validation

`find_by_title()` (`tools/literature.py:207`) exists to verify a paper the
*user cited*; it currently runs a full `federated_search` and takes the top hit.
The Graph API has `GET /paper/search/match` — returns the single best **title
match** with a match score, or 404 if none. That is exactly this task and is
cheaper/cleaner than a full federated search + 0.35 similarity heuristic.

1. Add `match_by_title(title)` to `tools/sources/semantic_scholar.py` calling
   `GET /paper/search/match` with the same fields and request headers; return the
   normalized paper (with the API's `matchScore`) or `None` on 404.
2. In `tools/literature.py:find_by_title()`, try `semantic_scholar.match_by_title`
   first; fall back to the existing `federated_search` path if it 404s or the
   Semantic Scholar host breaker is tripped (so non-S2-indexed cites still
   resolve). Keep the signature/return shape so the Librarian's validation
   records (`validated`/`unverified`) are unchanged.

### Phase 3 — Snippet Search as passage-level evidence (highest fit, larger effort)

`GET /snippet/search` returns relevant **text passages** (from abstracts *and*
open-access full text) with the passage `text`, a relevance `score`, and section
+ paper metadata. This is the closest match to "how the Librarian reads": today
it pulls a whole abstract and asks the LLM to hunt for a supporting quote;
snippet search hands back the relevant passage directly and reaches into
full-text bodies the abstract-only path can't see.

Recommended integration as a **quote-assist + extra source**, gated by config so
it can be rolled out cautiously:

1. New module `tools/sources/semantic_scholar_snippets.py`:
   `search_snippets(query, limit)` → `GET /snippet/search`
   (`fields="snippet.text,snippet.snippetKind,snippet.section,score,paper.title,paper.externalIds,paper.year,paper.corpusId"`),
   normalized to records carrying `snippet_text`, `score`, `section`, plus the
   same paper-identity fields used elsewhere so they dedupe by DOI/title.
2. Wire into the Librarian per-claim flow behind a config flag
   `[literature] use_snippet_search = false`:
   - when on, run snippet search on the top 1–2 expanded query variants;
   - attach the best-scoring returned passage to the matching retrieved paper,
     letting the classifier confirm rather than hunt — and surfacing full-text
     evidence for papers whose abstract was inconclusive. Full-text evidence is
     stored in `snippet_quote`; `justification_quote` remains abstract-only.
3. Respect the existing `SourceHealth` breaker (new host = `api.semanticscholar.org`
   shared with `/paper/search`) and the per-claim wall-clock budget; snippet
   search is an *addition* inside the existing timeout envelope, never a blocker.

**Caveats to honor:** snippet search is rate-limited harder without an API key
(observed 429 during research), and only covers open-access full text + abstracts.
Hence it is opt-in (`use_snippet_search = false` default) and degrades silently
via `SourceHealth` like every other source.

## Recommended scope

Phases 1 + 2 are low-risk, touch one source module plus one helper, and
immediately improve relevance and cited-paper validation — do these first.
Phase 3 is the conceptual centerpiece ("how the Librarian reads") but is more
code and depends on rate-limit behavior; ship it behind the config flag after
1 + 2 are verified.

## Verification

- **Unit / smoke:** with `SEMANTIC_SCHOLAR_API_KEY` set, call
  `semantic_scholar.search("blood brain barrier synapse")` and confirm the
  normalized dict now carries `tldr`/`open_access_pdf`/`fields_of_study`, and
  that `fieldsOfStudy=Biology,Medicine` is sent (inspect `params`).
- **Match endpoint:** `semantic_scholar.match_by_title("<a known paper title>")`
  returns one record with a match score; a nonsense title returns `None` (404).
- **End-to-end CLI:**
  `PYTHONPATH=backend python -m nullifier.cli run --input examples/synapse_bbb.txt --no-confirm --debug`
  — confirm `papers_retrieved`/`paper_classified` events still fire, quotes are
  present, and (Phase 3 on) some quotes trace to full-text snippets.
- **Degradation:** unset the API key and/or force a 429 and confirm the
  `SourceHealth` breaker skips Semantic Scholar without aborting the run, and
  `find_by_title` falls back to federated search.
- **Regression:** run with all new flags at defaults (snippet search off) and
  confirm behavior matches current `main` aside from the added `tldr` field.
