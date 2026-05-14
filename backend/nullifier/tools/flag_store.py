import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".nullifier" / "flags.db"


def _init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            hypothesis_summary TEXT NOT NULL,
            domain TEXT,
            entities_json TEXT,
            paper_title TEXT NOT NULL,
            paper_abstract_excerpt TEXT NOT NULL,
            agent_classification TEXT NOT NULL,
            agent_justification TEXT NOT NULL,
            user_classification TEXT NOT NULL,
            user_reason TEXT
        )
    """)
    conn.commit()
    return conn


def add_flag(hypothesis_summary: str, domain: str, entities: list[str],
             paper_title: str, paper_abstract_excerpt: str,
             agent_classification: str, agent_justification: str,
             user_classification: str, user_reason: str = ""):
    conn = _init_db()
    conn.execute("""
        INSERT INTO flags (created_at, hypothesis_summary, domain, entities_json,
                           paper_title, paper_abstract_excerpt,
                           agent_classification, agent_justification,
                           user_classification, user_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), hypothesis_summary, domain,
          json.dumps(entities), paper_title, paper_abstract_excerpt[:500],
          agent_classification, agent_justification,
          user_classification, user_reason))
    conn.commit()
    conn.close()


def get_relevant_flags(hypothesis_summary: str, domain: str,
                       entities: list[str], max_flags: int = 5) -> list[dict]:
    conn = _init_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM flags WHERE domain = ? ORDER BY created_at DESC LIMIT 50
    """, (domain,)).fetchall()
    conn.close()

    if not rows:
        return []

    entity_set = {e.lower() for e in entities}
    scored = []
    for row in rows:
        row_entities = set(e.lower() for e in json.loads(row["entities_json"] or "[]"))
        overlap = len(entity_set & row_entities)
        hypothesis_overlap = len(
            set(hypothesis_summary.lower().split()) &
            set(row["hypothesis_summary"].lower().split())
        )
        score = overlap * 10 + hypothesis_overlap
        if score > 0:
            scored.append((score, dict(row)))

    scored.sort(reverse=True)
    return [r for _, r in scored[:max_flags]]


def list_all_flags() -> list[dict]:
    conn = _init_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM flags ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def format_flags_for_prompt(flags: list[dict]) -> str:
    if not flags:
        return ""
    lines = ["PAST CORRECTIONS (learn from these):\n"]
    for i, f in enumerate(flags, 1):
        lines.append(f"Example {i}:")
        lines.append(f'  Paper: "{f["paper_title"]}"')
        lines.append(f'  Abstract: "{f["paper_abstract_excerpt"][:300]}..."')
        lines.append(f"  You classified: {f['agent_classification']}")
        lines.append(f"  Your justification: {f['agent_justification']}")
        lines.append(f"  CORRECT classification: {f['user_classification']}")
        if f["user_reason"]:
            lines.append(f"  Reason for correction: {f['user_reason']}")
        lines.append("")
    lines.append("Apply these lessons when classifying papers in this run.\n")
    return "\n".join(lines)
