import threading
import re
from concurrent.futures import ThreadPoolExecutor, wait as _cf_wait

import requests

from .sources import semantic_scholar, openalex, europe_pmc, biorxiv

SOURCES = {
    "semantic_scholar": semantic_scholar.search,
    "openalex": openalex.search,
    "europe_pmc": europe_pmc.search,
    "biorxiv": biorxiv.search,
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "into",
    "is", "it", "of", "on", "or", "the", "to", "with", "without",
}

# Source -> backing host. europe_pmc and biorxiv share www.ebi.ac.uk, so one
# EBI outage should disable both at once.
SOURCE_HOST = {
    "semantic_scholar": "api.semanticscholar.org",
    "openalex": "api.openalex.org",
    "europe_pmc": "www.ebi.ac.uk",
    "biorxiv": "www.ebi.ac.uk",
}

# Per-source-call wall-clock cap inside federated_search (one budget for the
# whole fan-out, not per source).
_FAN_OUT_TIMEOUT = 10.0
# Soft failures (e.g. an odd 4xx) needed to trip a host. A connection/timeout
# error or a 5xx/429 trips on the first occurrence — the service is clearly down
# or throttling, no point hammering it for the rest of the run.
_TRIP_THRESHOLD = 2


def _is_hard_failure(exc: BaseException | None) -> bool:
    if isinstance(exc, (requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
        return code is not None and (code >= 500 or code == 429)
    return False


class SourceHealth:
    """Per-run circuit breaker keyed by host.

    Create one per ``retrieve_evidence`` call and thread it through every
    ``federated_search`` call in that run. Thread-safe (searches now fan out
    concurrently); NOT shared across runs — the server may execute pipelines
    concurrently in separate threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}   # host -> consecutive failure count
        self._tripped: set[str] = set()       # hosts we've given up on for this run

    def is_open(self, source: str) -> bool:
        """True if the source's host has tripped and should be skipped."""
        host = SOURCE_HOST.get(source)
        with self._lock:
            return host in self._tripped

    def record_ok(self, source: str) -> None:
        host = SOURCE_HOST.get(source)
        if not host:
            return
        with self._lock:
            self._failures[host] = 0

    def record_fail(self, source: str, exc: BaseException | None = None) -> None:
        host = SOURCE_HOST.get(source)
        if not host:
            return
        hard = _is_hard_failure(exc)
        with self._lock:
            n = self._failures.get(host, 0) + 1
            self._failures[host] = n
            if hard or n >= _TRIP_THRESHOLD:
                self._tripped.add(host)

    def tripped_hosts(self) -> set[str]:
        with self._lock:
            return set(self._tripped)


def federated_search(query: str, max_per_source: int = 5,
                     health: "SourceHealth | None" = None) -> tuple[list[dict], dict]:
    """Search all (live) sources concurrently. Returns ``(deduped_papers, status)``.

    If ``health`` is supplied, sources whose host has tripped the breaker are
    skipped, and successes/failures are reported back to it.
    """
    status: dict[str, str] = {}
    active = {}
    for name, fn in SOURCES.items():
        if health and health.is_open(name):
            status[name] = f"disabled (host {SOURCE_HOST[name]} unresponsive)"
        else:
            active[name] = fn

    all_results: list[dict] = []
    if active:
        ex = ThreadPoolExecutor(max_workers=min(len(active), 2))
        try:
            fut_to_name = {ex.submit(fn, query, max_per_source): name
                           for name, fn in active.items()}
            done, not_done = _cf_wait(fut_to_name, timeout=_FAN_OUT_TIMEOUT)
            for fut in done:
                name = fut_to_name[fut]
                try:
                    results = fut.result()
                    all_results.extend(results)
                    status[name] = f"ok ({len(results)} results)"
                    if health:
                        health.record_ok(name)
                except Exception as e:
                    status[name] = f"failed: {type(e).__name__}"
                    print(f"[{name}] {type(e).__name__}: {e}")
                    if health:
                        health.record_fail(name, e)
            for fut in not_done:
                name = fut_to_name[fut]
                status[name] = "failed: timeout"
                print(f"[{name}] timed out (> {_FAN_OUT_TIMEOUT:.0f}s)")
                fut.cancel()
                if health:
                    health.record_fail(name, requests.exceptions.Timeout())
        finally:
            # Don't block on stragglers — their underlying request is already
            # bounded by the per-source read timeout; let the threads drain.
            ex.shutdown(wait=False, cancel_futures=True)

    deduped = _dedupe(all_results)
    ranked = _rank(deduped)
    return ranked, status


def _dedupe(papers: list[dict]) -> list[dict]:
    seen_dois = {}
    seen_titles = {}
    for p in papers:
        key = None
        if p.get("doi"):
            key = ("doi", p["doi"].lower())
        else:
            title = (p.get("title") or "").lower().strip()
            if title:
                key = ("title", title[:100])
        if not key:
            continue
        if key[0] == "doi":
            existing = seen_dois.get(key)
            if existing is None or _paper_score(p) > _paper_score(existing):
                seen_dois[key] = p
        else:
            existing = seen_titles.get(key)
            if existing is None or _paper_score(p) > _paper_score(existing):
                seen_titles[key] = p
    return list(seen_dois.values()) + list(seen_titles.values())


def _paper_score(p: dict) -> float:
    score = 0.0
    if p.get("abstract"):
        score += 10
    if p.get("year"):
        score += min(p["year"] - 1990, 30)
    if p.get("citation_count"):
        score += min(p["citation_count"] / 10, 20)
    if p.get("influential_citation_count"):
        score += min(p["influential_citation_count"], 10)
    score += {"semantic_scholar": 3, "openalex": 2, "europe_pmc": 1, "biorxiv": 0}.get(p["source"], 0)
    return score


def _rank(papers: list[dict]) -> list[dict]:
    return sorted(papers, key=_paper_score, reverse=True)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 2}


def citation_similarity(query: str, paper: dict | None) -> float:
    """Cheap title/abstract similarity for validating user-cited references."""
    if not paper:
        return 0.0
    query_tokens = _tokens(query or "")
    if not query_tokens:
        return 0.0
    title_tokens = _tokens(paper.get("title") or "")
    abstract_tokens = _tokens(paper.get("abstract") or "")
    candidate = title_tokens | abstract_tokens
    if not candidate:
        return 0.0
    return len(query_tokens & candidate) / len(query_tokens)


def find_by_title(title_fragment: str, health: "SourceHealth | None" = None) -> dict | None:
    """Look up a specific paper the user cited by title fragment."""
    results, _ = federated_search(title_fragment[:200], max_per_source=3, health=health)
    return results[0] if results else None
