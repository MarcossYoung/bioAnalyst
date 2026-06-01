"""R/PAML startup health checks plus R-backed dN/dS helpers."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
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
_CACHE_PATH = Path.home() / ".nullifier" / "rdnds_cache.db"
_CACHE_TTL_DAYS = 90
_conn: sqlite3.Connection | None = None
_cache_lock = threading.Lock()
_r_lock = threading.Lock()


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

    if missing:
        return RHealth(
            False,
            True,
            "missing R package(s): " + ", ".join(missing),
            missing,
            configured_home or os.environ.get("R_HOME"),
            codeml_path,
        )

    _INITIALIZED = True
    codeml_note = (
        "codeml is available for secondary PAML branch-model omega"
        if codeml_path else
        "codeml binary not found on PATH; secondary PAML branch-model omega will be unavailable"
    )
    return RHealth(
        True,
        True,
        f"R initialized; required packages are available; {codeml_note}",
        [],
        configured_home or os.environ.get("R_HOME"),
        codeml_path,
    )


def health_check() -> dict[str, Any]:
    return initialize_r().to_dict()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(_CACHE_PATH), check_same_thread=False)
        c.execute("""
            CREATE TABLE IF NOT EXISTS rdnds_cache (
                cache_key  TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                cached_at  TEXT NOT NULL
            )
        """)
        c.commit()
        _conn = c
    return _conn


def _cache_get(key: str) -> dict[str, float] | None:
    with _cache_lock:
        row = _get_conn().execute(
            "SELECT value, cached_at FROM rdnds_cache WHERE cache_key=?", (key,)
        ).fetchone()
    if not row:
        return None
    cached_at = datetime.fromisoformat(row[1])
    if datetime.now(timezone.utc).replace(tzinfo=None) - cached_at > timedelta(days=_CACHE_TTL_DAYS):
        return None
    value = json.loads(row[0])
    return value if isinstance(value, dict) else None


def _cache_set(key: str, value: dict[str, float]) -> None:
    with _cache_lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO rdnds_cache (cache_key, value, cached_at) VALUES (?,?,?)",
            (key, json.dumps(value), datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
        )
        conn.commit()


def _prepared_sequences(sequences: dict[str, str], reference: str) -> dict[str, str] | None:
    if not isinstance(sequences, dict) or reference not in sequences:
        return None
    ref = (sequences.get(reference) or "").upper()
    if not ref or len(ref) % 3 != 0:
        return None
    out = {
        sp: (seq or "").upper()
        for sp, seq in sequences.items()
        if isinstance(sp, str) and isinstance(seq, str)
        and len(seq) == len(ref) and len(seq) % 3 == 0 and seq
    }
    if reference not in out or len(out) < 2:
        return None
    return out


def _cache_key(seqs: dict[str, str], reference: str) -> str:
    payload = json.dumps([reference, sorted(seqs.items())], separators=(",", ":"))
    return hashlib.sha1(payload.encode()).hexdigest()


def _configure_r_home() -> bool:
    cfg = load_config().get("r", {})
    if not bool(cfg.get("enabled", True)):
        return False
    configured_home = (cfg.get("r_home") or "").strip()
    if configured_home:
        os.environ["R_HOME"] = configured_home
    return True


def pairwise_dnds(
    sequences: dict[str, str],
    reference: str = "Homo_sapiens",
    use_cache: bool = True,
) -> dict[str, float] | None:
    """Compute pairwise dN/dS from an aligned CDS dict using seqinr::kaks.

    Returns lowercased species keys mapped to finite Ka/Ks values for the
    reference row. All failures return None so callers degrade to missing dN/dS.
    """
    seqs = _prepared_sequences(sequences, reference)
    if not seqs:
        return None

    key = _cache_key(seqs, reference)
    if use_cache:
        cached = _cache_get(key)
        if cached:
            return cached

    if not _configure_r_home():
        return None

    try:
        from rpy2 import robjects
        from rpy2.robjects import StrVector
        from rpy2.robjects.packages import importr
    except Exception:
        return None

    try:
        names = list(seqs.keys())
        values = [seqs[name] for name in names]
        with _r_lock:
            importr("seqinr")
            r_fn = robjects.r("""
                function(nam, seq, ref) {
                  aln <- list(nb=length(seq), nam=as.character(nam), seq=as.character(seq), com="")
                  class(aln) <- "alignment"
                  kk <- seqinr::kaks(aln)

                  pick <- function(obj, keys) {
                    for (key in keys) {
                      if (is.list(obj) && !is.null(obj[[key]])) return(obj[[key]])
                    }
                    NULL
                  }

                  ka <- pick(kk, c("ka", "Ka", "KA"))
                  ks <- pick(kk, c("ks", "Ks", "KS"))
                  if (is.null(ka) && is.list(kk) && length(kk) >= 2) {
                    ka <- kk[[1]]
                    ks <- kk[[2]]
                  }
                  if (is.null(ka) || is.null(ks)) stop("unexpected kaks return shape")

                  ka <- as.matrix(ka)
                  ks <- as.matrix(ks)
                  if (is.null(rownames(ka))) rownames(ka) <- as.character(nam)
                  if (is.null(colnames(ka))) colnames(ka) <- as.character(nam)
                  if (is.null(rownames(ks))) rownames(ks) <- as.character(nam)
                  if (is.null(colnames(ks))) colnames(ks) <- as.character(nam)
                  if (!(ref %in% rownames(ka)) || !(ref %in% rownames(ks))) stop("reference missing")

                  out <- c()
                  for (sp in as.character(nam)) {
                    if (sp == ref) next
                    if (!(sp %in% colnames(ka)) || !(sp %in% colnames(ks))) next
                    dn <- suppressWarnings(as.numeric(ka[ref, sp]))
                    ds <- suppressWarnings(as.numeric(ks[ref, sp]))
                    if (is.finite(dn) && is.finite(ds) && ds > 0) {
                      out[sp] <- dn / ds
                    }
                  }
                  out
                }
            """)
            raw = r_fn(StrVector(names), StrVector(values), reference)
    except Exception:
        return None

    try:
        r_names = list(raw.names) if raw.names is not None else []
        result: dict[str, float] = {}
        for sp, val in zip(r_names, list(raw)):
            v = float(val)
            if math.isfinite(v) and v >= 0:
                result[sp.lower()] = round(v, 6)
    except Exception:
        return None

    if not result:
        return None
    if use_cache:
        _cache_set(key, result)
    return result

