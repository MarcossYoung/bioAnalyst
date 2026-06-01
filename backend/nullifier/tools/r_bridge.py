"""R/PAML startup health checks."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
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


def _find_rscript(configured_home: str) -> str | None:
    candidates: list[Path] = []
    if configured_home:
        r_home = Path(configured_home)
        candidates.extend([
            r_home / "bin" / "Rscript.exe",
            r_home / "bin" / "x64" / "Rscript.exe",
            r_home / "bin" / "Rscript",
        ])

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("Rscript")


def _find_codeml(configured_path: str) -> str | None:
    if configured_path:
        path = Path(configured_path).expanduser()
        if path.exists():
            return str(path)
    return shutil.which("codeml")


def _missing_r_packages(rscript: str, packages: list[str]) -> list[str]:
    script = (
        "packages <- commandArgs(trailingOnly=TRUE); "
        "missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly=TRUE)]; "
        "cat(paste(missing, collapse='\\n'))"
    )
    result = subprocess.run(
        [rscript, "-e", script, *packages],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Rscript package check failed").strip()
        raise RuntimeError(message)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def initialize_r() -> RHealth:
    """Verify required R packages plus codeml.

    The check uses ``Rscript`` rather than importing rpy2. On Windows, rpy2 can
    mis-detect R when Git's ``sh.exe`` is on PATH and fail before the app starts.
    """
    global _INITIALIZED
    full_cfg = load_config()
    cfg = full_cfg.get("r", {})
    enabled = bool(cfg.get("enabled", True))
    paml_cfg = full_cfg.get("paml", {})
    codeml_path = _find_codeml((paml_cfg.get("codeml_path") or "").strip())
    if not enabled:
        return RHealth(True, False, "R integration disabled", [], None, codeml_path)

    configured_home = (cfg.get("r_home") or "").strip()
    if configured_home:
        os.environ["R_HOME"] = configured_home

    required = list(cfg.get("required_packages") or ["ape", "phangorn", "seqinr", "caper"])
    rscript = _find_rscript(configured_home)
    if rscript is None:
        return RHealth(
            False,
            True,
            "Rscript not found. Install R 4.0+ or set [r].r_home.",
            required,
            configured_home or os.environ.get("R_HOME"),
            codeml_path,
        )

    try:
        missing = _missing_r_packages(rscript, required)
    except Exception as exc:
        return RHealth(
            False,
            True,
            f"R package check failed: {exc}",
            required,
            configured_home or os.environ.get("R_HOME"),
            codeml_path,
        )

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

