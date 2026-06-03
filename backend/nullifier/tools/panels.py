from __future__ import annotations

from functools import lru_cache
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load_lines(filename: str) -> list[str]:
    path = DATA_DIR / filename
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


@lru_cache(maxsize=1)
def mammal_panel() -> list[str]:
    return [s.lower() for s in _load_lines("mammal_panel.txt")]


@lru_cache(maxsize=1)
def random_background_genes() -> list[str]:
    return [g.upper() for g in _load_lines("random_background_300.txt")]
