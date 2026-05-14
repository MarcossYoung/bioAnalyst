"""Structured provenance for every claim, classification, test result, critique,
and score Nullifier v6 emits.

Each provenance record is a plain dict (kept JSON-serialisable for store/runs.py
and the WebSocket fan-out) shaped like ``Provenance`` below. ``make_provenance``
fills the cheap fields up-front; the optional ``enrich`` pass uses local Gemma
(``routing.provenance_enrichment``) to fill ``triggered_by`` / ``evidence_refs``
/ ``method`` / ``confidence`` where they're empty.

The intent is: every output the UI displays should be traceable to *why* the
agent produced it, on what inputs, and via what method — not as English prose
inside the same blob but as a separate, machine-readable record.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

PIPELINE_VERSION = "v6.0.0"
CALIBRATION_NOTE = "Heuristic score, not a calibrated probability."


@dataclass
class Provenance:
    source: str = ""
    triggered_by: list = field(default_factory=list)
    evidence_refs: list = field(default_factory=list)
    method: str = ""
    confidence: float = 0.0
    pipeline_version: str = PIPELINE_VERSION
    timestamp: str = ""
    input_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash_inputs(obj: Any) -> str:
    if obj is None:
        return ""
    try:
        s = json.dumps(obj, sort_keys=True, default=str)
    except (TypeError, ValueError):
        s = repr(obj)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def make_provenance(source: str, *, triggered_by: Iterable = (), evidence_refs: Iterable = (),
                    method: str = "", confidence: float = 0.0, inputs: Any = None) -> dict:
    """Build a fresh provenance record. Cheap fields are filled here; gaps left for
    ``enrich`` to fill from context."""
    return {
        "source": source,
        "triggered_by": list(triggered_by),
        "evidence_refs": list(evidence_refs),
        "method": method,
        "confidence": float(confidence) if confidence is not None else 0.0,
        "pipeline_version": PIPELINE_VERSION,
        "timestamp": _iso_now(),
        "input_hash": _hash_inputs(inputs),
    }


def attach(target, prov: dict):
    """Attach the same provenance dict to one record or every record in a list.
    Returns ``target`` for chaining.

    Records that already have a non-empty ``provenance`` field are left alone."""
    if isinstance(target, list):
        for item in target:
            if isinstance(item, dict) and not item.get("provenance"):
                item["provenance"] = dict(prov)
        return target
    if isinstance(target, dict) and not target.get("provenance"):
        target["provenance"] = dict(prov)
    return target


# ── enrichment (batched Gemma pass) ─────────────────────────────────────────
_ENRICH_SYSTEM = """You enrich a structured provenance record for a Nullifier v6 output.

You receive: (1) the partially-filled provenance, (2) a short context blob (the agent's
actual output the provenance describes), and (3) the surrounding inputs the agent saw.

Fill ONLY the four enrichable fields, leaving anything else untouched. Be concrete and
brief — these go into a UI chip, not a paragraph:

  triggered_by   — list[str]: ids/refs of the specific inputs that caused this output
                   (e.g. claim ids, paper ids, gene symbols, GO terms, test names).
  evidence_refs  — list[str]: external references the output ultimately rests on
                   (paper titles, DOIs, gene set names, Ensembl, GO terms, statistic names).
  method         — str: one short clause describing how the output was produced.
  confidence     — float 0..1: a calibrated guess at how trustworthy this single output is.
                   Be honest — 0.4-0.6 is the right range for most LLM judgments.

Respond with ONLY valid JSON: {"triggered_by":[...], "evidence_refs":[...], "method":"...", "confidence":0.0}"""


def _enrich_one(prov: dict, output_blob: Any, context_blob: Any) -> dict:
    from .tools.llm_client import llm_call_json  # local: avoid cycle on package import
    user = (f"PROVENANCE (partial):\n{json.dumps(prov, default=str)[:1200]}\n\n"
            f"OUTPUT THE PROVENANCE DESCRIBES:\n{json.dumps(output_blob, default=str)[:1200]}\n\n"
            f"SURROUNDING CONTEXT:\n{json.dumps(context_blob, default=str)[:1500]}\n")
    try:
        out = llm_call_json("provenance_enrichment", _ENRICH_SYSTEM, user, max_tokens=400)
    except Exception:
        return prov  # leave as-is on failure — never block the pipeline
    enriched = dict(prov)
    if isinstance(out.get("triggered_by"), list) and out["triggered_by"]:
        enriched["triggered_by"] = [str(x) for x in out["triggered_by"]][:8]
    if isinstance(out.get("evidence_refs"), list) and out["evidence_refs"]:
        enriched["evidence_refs"] = [str(x) for x in out["evidence_refs"]][:12]
    m = out.get("method")
    if isinstance(m, str) and m.strip():
        enriched["method"] = m.strip()[:240]
    c = out.get("confidence")
    if isinstance(c, (int, float)):
        enriched["confidence"] = max(0.0, min(1.0, float(c)))
    return enriched


def enrich(records: list[tuple[dict, Any, Any]]) -> list[dict]:
    """Batched enrichment. ``records`` is a list of ``(provenance, output, context)``
    triples; returns the enriched provenance dicts in input order."""
    return [_enrich_one(p, o, c) for (p, o, c) in records]
