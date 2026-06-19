import requests
import tomllib

from nullifier import events
from nullifier.agents import librarian
from nullifier.config.loader import DEFAULT_CONFIG
from nullifier.tools import literature
from nullifier.tools.sources import semantic_scholar, semantic_scholar_snippets


class FakeResponse:
    def __init__(self, payload=None, status_code=200, headers=None):
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}
        self.response = self

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"status {self.status_code}")
            error.response = self
            raise error


def test_default_config_exposes_librarian_integration_settings():
    with open(DEFAULT_CONFIG, "rb") as config_file:
        config = tomllib.load(config_file)

    assert config["routing"]["librarian_hunter"] == "claude"
    assert config["literature"]["hunt_max_rounds"] == 3
    assert config["literature"]["use_snippet_search"] is False


def test_hunt_round_event_has_stable_payload():
    event = events.hunt_round("c1", 1, 2, 3)

    assert event.type == "hunt_round"
    assert event.payload == {
        "claim_id": "c1",
        "round_index": 1,
        "contradicting_count": 2,
        "new_paper_count": 3,
    }


def test_search_sends_filters_and_normalizes_enriched_fields(monkeypatch):
    captured = {}
    payload = {
        "data": [{
            "paperId": "p1",
            "title": "Paper",
            "abstract": "Evidence.",
            "year": 2025,
            "externalIds": {"DOI": "10.1/example"},
            "tldr": {"text": "Short summary."},
            "openAccessPdf": {"url": "https://example.test/paper.pdf"},
            "publicationTypes": ["JournalArticle"],
            "publicationDate": "2025-01-02",
            "s2FieldsOfStudy": [{"category": "Biology"}],
        }]
    }

    def fake_get(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse(payload)

    monkeypatch.setattr(semantic_scholar, "load_config", lambda: {"literature": {
        "semantic_scholar_fields_of_study": "Biology,Medicine",
        "semantic_scholar_min_citations": 3,
    }})
    monkeypatch.setattr(semantic_scholar.requests, "get", fake_get)
    monkeypatch.setattr(semantic_scholar.time, "sleep", lambda *_: None)

    papers = semantic_scholar.search("blood brain barrier", 4)

    assert captured["url"].endswith("/paper/search")
    assert captured["params"]["fieldsOfStudy"] == "Biology,Medicine"
    assert captured["params"]["minCitationCount"] == 3
    assert papers[0]["tldr"] == "Short summary."
    assert papers[0]["open_access_pdf"].endswith("paper.pdf")
    assert papers[0]["publication_types"] == ["JournalArticle"]
    assert papers[0]["fields_of_study"] == ["Biology"]


def test_match_by_title_returns_match_score_and_handles_404(monkeypatch):
    responses = iter([
        FakeResponse({"data": [{
            "paperId": "p1",
            "title": "Exact paper",
            "abstract": "Evidence.",
            "externalIds": {},
            "matchScore": 0.97,
        }]}),
        FakeResponse(status_code=404),
    ])
    monkeypatch.setattr(semantic_scholar.requests, "get", lambda *args, **kwargs: next(responses))

    assert semantic_scholar.match_by_title("Exact paper")["match_score"] == 0.97
    assert semantic_scholar.match_by_title("nonsense") is None


def test_find_by_title_falls_back_after_match_failure(monkeypatch):
    fallback = {"source": "openalex", "id": "x", "title": "Fallback", "abstract": "A"}
    monkeypatch.setattr(
        semantic_scholar,
        "match_by_title",
        lambda *_: (_ for _ in ()).throw(requests.Timeout()),
    )
    monkeypatch.setattr(literature, "federated_search", lambda *args, **kwargs: ([fallback], {}))
    health = literature.SourceHealth()

    assert literature.find_by_title("Paper", health) == fallback
    assert "api.semanticscholar.org" in health.tripped_hosts()


def test_snippet_search_normalizes_passage(monkeypatch):
    payload = {"data": [{
        "score": 0.88,
        "snippet": {"text": "Relevant passage.", "snippetKind": "body", "section": "Results"},
        "paper": {
            "title": "Paper",
            "externalIds": {"DOI": "10.1/example"},
            "year": 2024,
            "corpusId": 123,
        },
    }]}
    monkeypatch.setattr(
        semantic_scholar_snippets.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    snippets = semantic_scholar_snippets.search_snippets("query", limit=2)

    assert snippets[0]["snippet_text"] == "Relevant passage."
    assert snippets[0]["snippet_section"] == "Results"
    assert snippets[0]["snippet_score"] == 0.88


def test_merge_snippets_keeps_passage_provenance_separate():
    papers = [{
        "source": "semantic_scholar",
        "id": "p1",
        "doi": "10.1/example",
        "title": "Paper",
        "abstract": "Abstract evidence.",
    }]
    snippets = [{
        "source": "semantic_scholar",
        "id": "123",
        "doi": "10.1/example",
        "title": "Paper",
        "abstract": "",
        "snippet_text": "Full-text evidence.",
        "snippet_section": "Results",
        "snippet_score": 0.9,
    }]

    merged = librarian._merge_snippet_evidence(papers, snippets, {"10.1/example"}, 12)

    assert len(merged) == 1
    assert merged[0]["abstract"] == "Abstract evidence."
    assert merged[0]["snippet_text"] == "Full-text evidence."


def test_classifier_keeps_abstract_and_snippet_quotes_separate(monkeypatch):
    paper = {
        "source": "semantic_scholar",
        "id": "p1",
        "title": "Paper",
        "abstract": "Abstract evidence.",
        "snippet_text": "Full-text evidence.",
    }
    monkeypatch.setattr(librarian, "llm_call_json_batch", lambda *args, **kwargs: [{
        "classification": "contradicts",
        "justification_quote": "Full-text evidence.",
        "snippet_quote": "Full-text evidence.",
        "quote_source": "abstract",
        "reasoning": "Matched",
    }])

    classifications, failures = librarian._classify_round(
        {"id": "c1", "statement": "claim", "null_hypothesis": "null"},
        [paper],
        "system",
        "",
        None,
    )

    assert failures == []
    assert classifications[0]["justification_quote"] == ""
    assert classifications[0]["snippet_quote"] == "Full-text evidence."
    assert classifications[0]["quote_source"] == "snippet"
