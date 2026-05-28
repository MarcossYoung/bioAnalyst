"""Small rpy2 bridge used for startup health checks and R-backed helpers."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, asdict
from typing import Any

from ..config.loader import load_config


@dataclass
class RHealth:
    ok: bool
    enabled: bool
    message: str
    missing_packages: list[str]
    r_home: str | None
    codeml_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_INITIALIZED = False


def initialize_r() -> RHealth:
    """Initialize rpy2 once and verify required R packages plus codeml.

    This function is intentionally side-effect-light: it only sets ``R_HOME`` when
    configured and imports rpy2. Callers can use the returned object for API
    health responses or fail-fast CLI checks.
    """
    global _INITIALIZED
    cfg = load_config().get("r", {})
    enabled = bool(cfg.get("enabled", True))
    codeml_path = shutil.which("codeml")
    if not enabled:
        return RHealth(True, False, "R integration disabled", [], None, codeml_path)

    configured_home = (cfg.get("r_home") or "").strip()
    if configured_home:
        os.environ["R_HOME"] = configured_home

    try:
        from rpy2.robjects import r  # type: ignore
    except Exception as exc:
        return RHealth(
            False,
            True,
            f"R/rpy2 unavailable: {exc}. Install R 4.0+ and Python package rpy2.",
            list(cfg.get("required_packages") or []),
            configured_home or os.environ.get("R_HOME"),
            codeml_path,
        )

    required = list(cfg.get("required_packages") or ["ape", "phangorn", "seqinr", "caper"])
    missing: list[str] = []
    for package in required:
        try:
            installed = bool(r(f"requireNamespace('{package}', quietly=TRUE)")[0])
        except Exception:
            installed = False
        if not installed:
            missing.append(package)

    problems: list[str] = []
    if missing:
        problems.append("missing R package(s): " + ", ".join(missing))
    if codeml_path is None:
        problems.append("codeml binary not found on PATH")

    if problems:
        return RHealth(
            False,
            True,
            "; ".join(problems),
            missing,
            configured_home or os.environ.get("R_HOME"),
            codeml_path,
        )

    _INITIALIZED = True
    return RHealth(
        True,
        True,
        "R initialized; required packages and codeml are available",
        [],
        configured_home or os.environ.get("R_HOME"),
        codeml_path,
    )


def health_check() -> dict[str, Any]:
    return initialize_r().to_dict()

