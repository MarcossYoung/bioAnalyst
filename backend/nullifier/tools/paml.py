"""PAML codeml branch, site, and branch-site model runners.

Public runners always return typed status dictionaries and never propagate
CODEML, alignment, cache, or parsing failures to the pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scipy.stats import chi2

from ..config.loader import load_config

_CACHE_PATH = Path.home() / ".nullifier" / "paml_cache.db"
_CACHE_TTL_DAYS = 90
_PROCESS_OUTPUT_LIMIT = 2000
_MIN_SEQUENCES = 3
_conn: sqlite3.Connection | None = None
_lock = threading.RLock()
logger = logging.getLogger(__name__)

_FOREGROUND_GROUPS = {
    "primates": {"Homo_sapiens", "Pan_troglodytes", "Gorilla_gorilla", "Macaca_mulatta", "Papio_anubis"},
    "rodents": {"Mus_musculus", "Rattus_norvegicus"},
    "cetaceans": {"Tursiops_truncatus", "Orcinus_orca", "Physeter_catodon"},
    "chiroptera": {"Myotis_lucifugus", "Pteropus_vampyrus"},
    "human": {"Homo_sapiens"},
}
_MAMMAL_SPECIES = set().union(*_FOREGROUND_GROUPS.values(), {
    "Bos_taurus", "Sus_scrofa", "Canis_lupus_familiaris", "Felis_catus", "Equus_caballus"
})


def _paml_config() -> dict:
    return load_config().get("paml", {})


def _find_codeml() -> str | None:
    configured = str(_paml_config().get("codeml_path", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return str(path)
    return shutil.which("codeml")


def _timeout_seconds() -> int:
    try:
        return max(1, int(_paml_config().get("timeout_seconds", 300)))
    except (TypeError, ValueError):
        return 300


def _get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(_CACHE_PATH), check_same_thread=False)
            _conn.execute("CREATE TABLE IF NOT EXISTS paml_cache (cache_key TEXT PRIMARY KEY, value TEXT NOT NULL, cached_at TEXT NOT NULL)")
            _conn.commit()
    return _conn


def _cache_get(key: str) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT value, cached_at FROM paml_cache WHERE cache_key=?", (key,)).fetchone()
    if not row:
        return None
    cached_at = datetime.fromisoformat(row[1])
    if datetime.now(timezone.utc).replace(tzinfo=None) - cached_at > timedelta(days=_CACHE_TTL_DAYS):
        return None
    return json.loads(row[0])


def _cache_set(key: str, value: dict) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("INSERT OR REPLACE INTO paml_cache (cache_key, value, cached_at) VALUES (?,?,?)", (key, json.dumps(value), datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
        conn.commit()


def _write_phylip(sequences: dict, path: str) -> None:
    items = list(sequences.items())
    seq_len = len(items[0][1]) if items else 0
    with open(path, "w") as handle:
        handle.write(f" {len(items)}  {seq_len}\n")
        for species, seq in items:
            handle.write(f"{species[:10].ljust(10)}  {seq}\n")


def _label_newick(newick: str, foreground_set: set) -> str:
    def replace(match):
        return f"{match.group(1)} #1{match.group(2)}" if match.group(1) in foreground_set else match.group(0)
    return re.sub(r"([A-Za-z][A-Za-z0-9_]*)(:\s*[\d.Ee+\-]+)", replace, newick)


def _write_control(workdir: str, model: int, seqfile: str, treefile: str, outfile: str,
                   *, nssites: int = 0, ncatg: int = 10, fix_omega: int = 0,
                   omega: float = 1.0, run_label: str | None = None) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]", "_", run_label or f"model_{model}_{nssites}")
    ctl = os.path.join(workdir, f"codeml_{label}.ctl")
    content = (
        f"seqfile  = {seqfile}\n" f"treefile = {treefile}\n" f"outfile  = {outfile}\n"
        "noisy    = 0\nverbose  = 0\nrunmode  = 0\nseqtype  = 1\nCodonFreq = 2\n"
        f"model    = {model}\nNSsites  = {nssites}\nncatG    = {ncatg}\n"
        "icode    = 0\nfix_kappa = 0\nkappa    = 2\n"
        f"fix_omega = {fix_omega}\nomega    = {omega}\n"
    )
    with open(ctl, "w") as handle:
        handle.write(content)
    return ctl


def _bounded_process_output(value, workdir: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).replace(workdir, "<workdir>")[-_PROCESS_OUTPUT_LIMIT:]


def _run_codeml(ctl_path: str, workdir: str, timeout: int = 300) -> dict:
    binary = _find_codeml()
    if not binary:
        return {"status": "unavailable", "note": "codeml executable is unavailable"}
    try:
        result = subprocess.run([binary, ctl_path], cwd=workdir, timeout=timeout, capture_output=True)
        diagnostic = {"status": "ok" if result.returncode == 0 else "error", "returncode": result.returncode,
                      "stdout": _bounded_process_output(result.stdout, workdir), "stderr": _bounded_process_output(result.stderr, workdir)}
        if result.returncode:
            diagnostic["note"] = "codeml exited with a nonzero status"
        return diagnostic
    except subprocess.TimeoutExpired as exc:
        return {"status": "timeout", "note": f"codeml timed out after {timeout} seconds",
                "stdout": _bounded_process_output(exc.stdout, workdir), "stderr": _bounded_process_output(exc.stderr, workdir)}
    except OSError as exc:
        return {"status": "error", "note": "codeml could not be executed: " + _bounded_process_output(exc, workdir)}


def _codeml_failure(gene: str, phase: str, diagnostic: dict, model: str | None = None) -> dict:
    return {"status": "timeout" if diagnostic.get("status") == "timeout" else "error", "gene": gene,
            **({"model": model} if model else {}), "phase": phase,
            "note": diagnostic.get("note") or f"codeml {phase} model failed",
            **{k: diagnostic[k] for k in ("returncode", "stdout", "stderr") if diagnostic.get(k) is not None}}


def _parse_lnl(mlc_path: str) -> float | None:
    try:
        with open(mlc_path) as handle:
            values = [float(line.rsplit(":", 1)[-1].strip().split()[0]) for line in handle if "lnL(" in line]
        return values[-1] if values else None
    except (OSError, ValueError, IndexError):
        return None


def _branch_omegas(mlc_path: str) -> list[float]:
    try:
        lines = Path(mlc_path).read_text(errors="replace").splitlines()
    except OSError:
        return []
    for i, line in enumerate(lines):
        if "dN/dS) for branches" in line:
            nums = []
            for candidate in lines[i:i + 5]:
                nums.extend(float(x) for x in re.findall(r"(?<![A-Za-z])[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?", candidate))
            return nums
    return []


def _parse_omega_foreground(mlc_path: str) -> float | None:
    values = _branch_omegas(mlc_path)
    return values[-1] if values else None


def _parse_omega_background(mlc_path: str) -> float | None:
    values = _branch_omegas(mlc_path)
    return values[0] if values else None


def _parse_site_classes(mlc_path: str) -> dict:
    """Parse positive-class omega and proportion from M8/branch-site output."""
    try:
        text = Path(mlc_path).read_text(errors="replace")
    except OSError:
        return {"omega": None, "proportion": None}
    props = []
    omegas = []
    for line in text.splitlines():
        lower = line.lower().strip()
        nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?", line)]
        if lower.startswith("p:") or lower.startswith("proportion"):
            props = nums
        if lower.startswith(("w:", "omega", "foreground w")) or "site class" in lower and "omega" in lower:
            omegas = nums
    return {"omega": omegas[-1] if omegas else None, "proportion": props[-1] if props else None}


def _parse_beb_sites(mlc_path: str) -> list[dict]:
    try:
        lines = Path(mlc_path).read_text(errors="replace").splitlines()
    except OSError:
        return []
    in_beb = False
    sites = []
    pattern = re.compile(r"^\s*(\d+)\s+(?:([A-Za-z*?\-])\s+)?(0?\.\d+|1(?:\.0+)?)\s*([*]{0,2})")
    for line in lines:
        if "Bayes Empirical Bayes" in line:
            in_beb = True
            continue
        if not in_beb:
            continue
        match = pattern.match(line)
        if match:
            sites.append({"position": int(match.group(1)), "amino_acid": match.group(2),
                          "posterior": float(match.group(3)), "significance_marker": match.group(4) or None})
        elif sites and not line.strip():
            break
    return sites


def _prepare(gene: str, aligned: dict, foreground: str | None = None) -> tuple[dict | None, dict | None]:
    seqs = {sp: seq for sp, seq in (aligned.get("sequences") or {}).items()
            if sp in _MAMMAL_SPECIES and isinstance(seq, str) and seq and len(seq) % 3 == 0}
    fg_present: set[str] = set()
    if foreground:
        fg_set = _FOREGROUND_GROUPS.get(foreground, _FOREGROUND_GROUPS["primates"])
        fg_present = set(seqs) & fg_set
        if not fg_present:
            return None, {"status": "no_foreground_seqs", "gene": gene, "note": f"no {foreground} species in Compara alignment"}
    if len(seqs) < _MIN_SEQUENCES:
        return None, {"status": "insufficient_sequences", "gene": gene, "note": f"need at least {_MIN_SEQUENCES} usable codon sequences"}
    if foreground:
        if not (set(seqs) - fg_present):
            return None, {"status": "insufficient_sequences", "gene": gene, "note": "foreground models require background sequences"}
    return {"seqs": seqs, "foreground": fg_present, "alignment_hash": hashlib.sha1(json.dumps(sorted(seqs.items())).encode()).hexdigest()[:12]}, None


def _cache_key(family: str, ensembl_id: str, prep: dict, foreground: str | None = None) -> str:
    return ":".join(x for x in (family, ensembl_id, foreground, prep["alignment_hash"]) if x)


def _base_result(gene: str, model: str, prep: dict, aligned: dict) -> dict:
    seqs = prep["seqs"]
    return {"status": "computed", "gene": gene, "model": model, "species_count": len(seqs),
            "alignment_length": len(next(iter(seqs.values()))) // 3,
            "provenance": {"paml": "codeml", "alignment_source": aligned.get("source", "ensembl_compara_genetree"),
                           "tree_source": aligned.get("tree_source", "ensembl_compara_genetree")}}


def _run_pair(ensembl_id: str, gene: str, aligned: dict, family: str, foreground: str | None,
              null_params: dict, alt_params: dict, use_cache: bool) -> tuple[dict | None, dict | None, dict | None]:
    prep, failure = _prepare(gene, aligned, foreground)
    if failure:
        failure["model"] = family
        return None, None, failure
    key = _cache_key(family, ensembl_id, prep, foreground)
    if use_cache and (hit := _cache_get(key)):
        return prep, hit, None
    if not _find_codeml():
        return None, None, {"status": "codeml_unavailable", "gene": gene, "model": family, "note": "codeml executable is unavailable"}
    workdir = tempfile.mkdtemp(prefix="paml_")
    try:
        seq_path, tree_path = os.path.join(workdir, "aln.phy"), os.path.join(workdir, "tree.nwk")
        _write_phylip(prep["seqs"], seq_path)
        tree = _label_newick(aligned.get("newick", ""), prep["foreground"]) if foreground else aligned.get("newick", "")
        Path(tree_path).write_text(tree)
        paths = {}
        for phase, params in (("null", null_params), ("alternative", alt_params)):
            outfile = f"{family}_{phase}.mlc"
            ctl = _write_control(workdir, seqfile=seq_path, treefile=tree_path, outfile=outfile,
                                 run_label=f"{family}_{phase}", **params)
            diagnostic = _run_codeml(ctl, workdir, timeout=_timeout_seconds())
            if diagnostic.get("status") != "ok":
                return None, None, _codeml_failure(gene, phase, diagnostic, family)
            paths[phase] = os.path.join(workdir, outfile)
        parsed = {"lnl_null": _parse_lnl(paths["null"]), "lnl_alt": _parse_lnl(paths["alternative"]),
                  "alt_path": paths["alternative"], "cache_key": key}
        if parsed["lnl_null"] is None or parsed["lnl_alt"] is None:
            return None, None, {"status": "error", "gene": gene, "model": family, "phase": "output_parse", "note": "codeml output unreadable"}
        # Parse model-specific output before the temporary directory is removed.
        parsed["classes"] = _parse_site_classes(paths["alternative"])
        parsed["beb_sites"] = _parse_beb_sites(paths["alternative"])
        parsed["omega_fg"] = _parse_omega_foreground(paths["alternative"])
        parsed["omega_bg"] = _parse_omega_background(paths["alternative"])
        return prep, parsed, None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_branch_model(ensembl_id: str, gene_symbol: str, aligned: dict, foreground: str = "primates", use_cache: bool = True) -> dict:
    try:
        prep, parsed, failure = _run_pair(ensembl_id, gene_symbol, aligned, "branch", foreground,
            {"model": 0, "nssites": 0}, {"model": 2, "nssites": 0}, use_cache)
        if failure:
            return failure
        if parsed.get("status") == "computed":
            return parsed
        lrt = max(2 * (parsed["lnl_alt"] - parsed["lnl_null"]), 0.0)
        fg, bg = parsed["omega_fg"], parsed["omega_bg"]
        result = {**_base_result(gene_symbol, "branch", prep, aligned), "omega_foreground": fg, "omega_background": bg,
                  "acceleration_ratio": round(fg / bg, 6) if fg is not None and bg not in (None, 0) else None,
                  "lrt_statistic": round(lrt, 4), "lrt_chi2": round(lrt, 4), "lrt_pvalue": round(float(chi2.sf(lrt, 1)), 6),
                  "n_species": len(prep["seqs"]), "foreground_label": foreground, "foreground_group": foreground,
                  "foreground_species": sorted(prep["foreground"]), "background_species": sorted(set(prep["seqs"]) - prep["foreground"]),
                  "newick": aligned.get("newick")}
        result["provenance"]["control_file_hash"] = hashlib.sha1(parsed["cache_key"].encode()).hexdigest()[:12]
        if use_cache: _cache_set(parsed["cache_key"], result)
        return result
    except Exception as exc:
        logger.exception("PAML branch model failed for %s", gene_symbol)
        return {"status": "error", "gene": gene_symbol, "model": "branch", "note": str(exc)}


def run_site_model(ensembl_id: str, gene_symbol: str, aligned: dict, use_cache: bool = True) -> dict:
    try:
        prep, parsed, failure = _run_pair(ensembl_id, gene_symbol, aligned, "site", None,
            {"model": 0, "nssites": 7, "ncatg": 10}, {"model": 0, "nssites": 8, "ncatg": 10}, use_cache)
        if failure: return failure
        if parsed.get("status") == "computed": return parsed
        lrt = max(2 * (parsed["lnl_alt"] - parsed["lnl_null"]), 0.0)
        result = {**_base_result(gene_symbol, "site", prep, aligned), "lrt_statistic": round(lrt, 4),
                  "lrt_pvalue": round(float(chi2.sf(lrt, 2)), 6), "omega_positive_class": parsed["classes"]["omega"],
                  "prop_positive": parsed["classes"]["proportion"], "beb_sites": parsed["beb_sites"]}
        if use_cache: _cache_set(parsed["cache_key"], result)
        return result
    except Exception as exc:
        logger.exception("PAML site model failed for %s", gene_symbol)
        return {"status": "error", "gene": gene_symbol, "model": "site", "note": str(exc)}


def run_branch_site_model(ensembl_id: str, gene_symbol: str, aligned: dict, foreground: str = "primates", use_cache: bool = True) -> dict:
    try:
        prep, parsed, failure = _run_pair(ensembl_id, gene_symbol, aligned, "branch_site", foreground,
            {"model": 2, "nssites": 2, "fix_omega": 1, "omega": 1.0},
            {"model": 2, "nssites": 2, "fix_omega": 0, "omega": 1.5}, use_cache)
        if failure: return failure
        if parsed.get("status") == "computed": return parsed
        lrt = max(2 * (parsed["lnl_alt"] - parsed["lnl_null"]), 0.0)
        pvalue = 1.0 if lrt == 0 else 0.5 * float(chi2.sf(lrt, 1))
        omega = parsed["classes"]["omega"] if parsed["classes"]["omega"] is not None else parsed["omega_fg"]
        result = {**_base_result(gene_symbol, "branch_site", prep, aligned), "foreground_group": foreground,
                  "lrt_statistic": round(lrt, 4), "lrt_pvalue": round(pvalue, 6),
                  "omega_foreground_positive": omega, "prop_sites": parsed["classes"]["proportion"],
                  "beb_sites": parsed["beb_sites"]}
        if use_cache: _cache_set(parsed["cache_key"], result)
        return result
    except Exception as exc:
        logger.exception("PAML branch-site model failed for %s", gene_symbol)
        return {"status": "error", "gene": gene_symbol, "model": "branch_site", "note": str(exc)}
