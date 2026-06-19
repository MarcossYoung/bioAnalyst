import time

import requests

from .semantic_scholar import BASE, _headers


FIELDS = ",".join((
    "snippet.text",
    "snippet.snippetKind",
    "snippet.section",
    "score",
    "paper.title",
    "paper.externalIds",
    "paper.year",
    "paper.corpusId",
))
TIMEOUT = (4, 8)


def search_snippets(
    query: str,
    limit: int = 10,
    timeout_seconds: float | None = None,
) -> list[dict]:
    timeout = TIMEOUT
    if timeout_seconds is not None:
        bounded = max(0.1, float(timeout_seconds))
        timeout = (min(TIMEOUT[0], bounded), min(TIMEOUT[1], bounded))

    for attempt in range(2):
        response = requests.get(
            f"{BASE}/snippet/search",
            params={"query": query, "limit": limit, "fields": FIELDS},
            headers=_headers(),
            timeout=timeout,
        )
        if response.status_code == 429 and attempt == 0:
            retry_after = min(int(response.headers.get("Retry-After", 10)), 20)
            if timeout_seconds is not None and retry_after >= timeout_seconds:
                response.raise_for_status()
            time.sleep(retry_after)
            continue
        response.raise_for_status()
        return [_normalize(item) for item in response.json().get("data", [])]
    return []


def _normalize(item: dict) -> dict:
    snippet = item.get("snippet") or {}
    paper = item.get("paper") or {}
    external_ids = paper.get("externalIds") or {}
    return {
        "source": "semantic_scholar",
        "id": str(paper.get("corpusId") or ""),
        "doi": external_ids.get("DOI"),
        "title": paper.get("title", ""),
        "abstract": "",
        "year": paper.get("year"),
        "venue": None,
        "snippet_text": snippet.get("text", ""),
        "snippet_kind": snippet.get("snippetKind"),
        "snippet_section": snippet.get("section"),
        "snippet_score": item.get("score"),
    }
