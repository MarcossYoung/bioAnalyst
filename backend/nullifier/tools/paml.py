"""PAML codeml branch-model ω for lineage-specific selection."""
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config.loader import load_config

_CACHE_PATH = Path.home() / ".nullifier" / "paml_cache.db"
_CACHE_TTL_DAYS = 90

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()
logger = logging.getLogger(__name__)
_PROCESS_OUTPUT_LIMIT = 2000

_FOREGROUND_GROUPS = {
    "primates": {"Homo_sapiens", "Pan_troglodytes", "Gorilla_gorilla",
                 "Macaca_mulatta", "Papio_anubis"},
    "rodents":  {"Mus_musculus", "Rattus_norvegicus"},
    "cetaceans": {"Tursiops_truncatus", "Orcinus_orca", "Physeter_catodon"},
    "chiroptera": {"Myotis_lucifugus", "Pteropus_vampyrus"},
    "human":    {"Homo_sapiens"},
}

_MAMMAL_SPECIES = {
    "Homo_sapiens", "Pan_troglodytes", "Gorilla_gorilla", "Macaca_mulatta",
    "Papio_anubis", "Mus_musculus", "Rattus_norvegicus", "Bos_taurus",
    "Sus_scrofa", "Canis_lupus_familiaris", "Felis_catus", "Equus_caballus",
    "Tursiops_truncatus", "Orcinus_orca", "Physeter_catodon", "Myotis_lucifugus",
    "Pteropus_vampyrus",
}


def _find_codeml() -> str | None:
    configured = (
        load_config()
        .get("paml", {})
        .get("codeml_path", "")
    ).strip()
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return str(path)
    return shutil.which("codeml")


# ── cache ────────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(_CACHE_PATH), check_same_thread=False)
        c.execute("""
            CREATE TABLE IF NOT EXISTS paml_cache (
                cache_key  TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                cached_at  TEXT NOT NULL
            )
        """)
        c.commit()
        _conn = c
    return _conn


def _cache_get(key: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT value, cached_at FROM paml_cache WHERE cache_key=?", (key,)
    ).fetchone()
    if not row:
        return None
    cached_at = datetime.fromisoformat(row[1])
    if datetime.now(timezone.utc).replace(tzinfo=None) - cached_at > timedelta(days=_CACHE_TTL_DAYS):
        return None
    return json.loads(row[0])


def _cache_set(key: str, value: dict) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO paml_cache (cache_key, value, cached_at) VALUES (?,?,?)",
        (key, json.dumps(value), datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
    )
    conn.commit()


# ── PHYLIP / tree / control helpers ─────────────────────────────────────────

def _write_phylip(sequences: dict, path: str) -> None:
    """Write aligned sequences in strict PHYLIP format (10-char padded names)."""
    items = list(sequences.items())
    n = len(items)
    seq_len = len(items[0][1]) if items else 0
    with open(path, "w") as f:
        f.write(f" {n}  {seq_len}\n")
        for species, seq in items:
            name = (species[:10]).ljust(10)
            f.write(f"{name}  {seq}\n")


def _label_newick(newick: str, foreground_set: set) -> str:
    """Append ' #1' to each foreground species name before its ':' in the Newick string."""
    import re
    def _replacer(m):
        sp = m.group(1)
        rest = m.group(2)
        if sp in foreground_set:
            return f"{sp} #1{rest}"
        return m.group(0)
    return re.sub(r"([A-Za-z][A-Za-z0-9_]*)(:\s*[\d.Ee+\-]+)", _replacer, newick)


def _write_control(workdir: str, model: int, seqfile: str, treefile: str, outfile: str) -> str:
    """Write a codeml.ctl file and return its path."""
    ctl = os.path.join(workdir, f"codeml_{model}.ctl")
    content = (
        f"seqfile  = {seqfile}\n"
        f"treefile = {treefile}\n"
        f"outfile  = {outfile}\n"
        "noisy    = 0\n"
        "verbose  = 0\n"
        "runmode  = 0\n"
        "seqtype  = 1\n"
        "CodonFreq = 2\n"
        f"model    = {model}\n"
        "NSsites  = 0\n"
        "icode    = 0\n"
        "fix_kappa = 0\n"
        "kappa    = 2\n"
        "fix_omega = 0\n"
        "omega    = 1\n"
    )
    with open(ctl, "w") as f:
        f.write(content)
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
        result = subprocess.run(
            [binary, ctl_path],
            cwd=workdir,
            timeout=timeout,
            capture_output=True,
        )
        diagnostic = {
            "status": "ok" if result.returncode == 0 else "error",
            "returncode": result.returncode,
            "stdout": _bounded_process_output(result.stdout, workdir),
            "stderr": _bounded_process_output(result.stderr, workdir),
        }
        if result.returncode != 0:
            diagnostic["note"] = "codeml exited with a nonzero status"
            logger.warning("codeml failed: %s", diagnostic)
        return diagnostic
    except subprocess.TimeoutExpired as exc:
        diagnostic = {
            "status": "timeout",
            "note": f"codeml timed out after {timeout} seconds",
            "stdout": _bounded_process_output(exc.stdout, workdir),
            "stderr": _bounded_process_output(exc.stderr, workdir),
        }
        logger.warning("codeml timed out: %s", diagnostic)
        return diagnostic
    except OSError as exc:
        diagnostic = {
            "status": "error",
            "note": "codeml could not be executed: " + _bounded_process_output(exc, workdir),
        }
        logger.warning("codeml execution error: %s", diagnostic)
        return diagnostic


def _codeml_failure(gene: str, phase: str, diagnostic: dict) -> dict:
    return {
        "status": "timeout" if diagnostic.get("status") == "timeout" else "error",
        "gene": gene,
        "phase": phase,
        "note": diagnostic.get("note") or f"codeml {phase} model failed",
        **{
            key: diagnostic[key]
            for key in ("returncode", "stdout", "stderr")
            if diagnostic.get(key) is not None
        },
    }


def _parse_lnl(mlc_path: str) -> float | None:
    """Scan codeml output for lnL line; return the float after the last ':'."""
    try:
        with open(mlc_path) as f:
            for line in f:
                if "lnL(" in line:
                    return float(line.rsplit(":", 1)[-1].strip())
    except (OSError, ValueError):
        pass
    return None


def _parse_omega_foreground(mlc_path: str) -> float | None:
    """Parse foreground ω from branch-model output (last value after 'dN/dS) for branches')."""
    try:
        with open(mlc_path) as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if "dN/dS) for branches" in line:
                nums = []
                for j in range(i, min(i + 5, len(lines))):
                    for tok in lines[j].split():
                        try:
                            nums.append(float(tok))
                        except ValueError:
                            pass
                if nums:
                    return nums[-1]
    except OSError:
        pass
    return None


def _parse_omega_background(mlc_path: str) -> float | None:
    """Parse background omega from branch-model output (first branch omega value)."""
    try:
        with open(mlc_path) as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if "dN/dS) for branches" in line:
                nums = []
                for j in range(i, min(i + 5, len(lines))):
                    for tok in lines[j].split():
                        try:
                            nums.append(float(tok))
                        except ValueError:
                            pass
                if nums:
                    return nums[0]
    except OSError:
        pass
    return None


# ── top-level function ───────────────────────────────────────────────────────

def run_branch_model(
    ensembl_id: str,
    gene_symbol: str,
    aligned: dict,
    foreground: str = "primates",
    use_cache: bool = True,
) -> dict:
    """Run PAML branch model 2 LRT. Returns a status dict; never raises.

    status values:
      "computed"            — LRT completed; omega_foreground + lrt_pvalue present
      "codeml_unavailable"  — binary not in PATH
      "no_foreground_seqs"  — no foreground species found in alignment
      "error"               — codeml ran but output was unreadable
    """
    fg_set = _FOREGROUND_GROUPS.get(foreground, _FOREGROUND_GROUPS["primates"])
    seqs = {
        sp: seq for sp, seq in aligned.get("sequences", {}).items()
        if sp in _MAMMAL_SPECIES and len(seq) % 3 == 0 and len(seq) > 0
    }
    fg_present = {sp for sp in seqs if sp in fg_set}
    bg_present = {sp for sp in seqs if sp not in fg_set}
    if not fg_present:
        return {
            "status": "no_foreground_seqs", "gene": gene_symbol,
            "note": f"no {foreground} species in Compara alignment",
        }

    aln_hash = hashlib.sha1(json.dumps(sorted(seqs.items())).encode()).hexdigest()[:10]
    cache_key = f"{ensembl_id}:{foreground}:{aln_hash}"
    if use_cache and (hit := _cache_get(cache_key)):
        return hit

    if not _find_codeml():
        return {
            "status": "codeml_unavailable",
            "gene": gene_symbol,
            "note": "codeml executable is unavailable",
        }

    with tempfile.TemporaryDirectory() as workdir:
        seq_path  = os.path.join(workdir, "aln.phy")
        tree_path = os.path.join(workdir, "tree.nwk")
        _write_phylip(seqs, seq_path)
        with open(tree_path, "w") as f:
            f.write(_label_newick(aligned.get("newick", ""), fg_present))

        # null model — one ratio (model=0)
        ctl0 = _write_control(workdir, 0, seq_path, tree_path, "null_mlc")
        status0 = _run_codeml(ctl0, workdir)
        if status0.get("status") != "ok":
            return _codeml_failure(gene_symbol, "null", status0)
        lnl0 = _parse_lnl(os.path.join(workdir, "null_mlc"))

        # alternative model — branch model 2
        ctl2 = _write_control(workdir, 2, seq_path, tree_path, "alt_mlc")
        status2 = _run_codeml(ctl2, workdir)
        if status2.get("status") != "ok":
            return _codeml_failure(gene_symbol, "alternative", status2)
        lnl2     = _parse_lnl(os.path.join(workdir, "alt_mlc"))
        omega_fg = _parse_omega_foreground(os.path.join(workdir, "alt_mlc"))
        omega_bg = _parse_omega_background(os.path.join(workdir, "alt_mlc"))

    if lnl0 is None or lnl2 is None:
        result = {
            "status": "error",
            "gene": gene_symbol,
            "phase": "output_parse",
            "note": "codeml output unreadable",
        }
        logger.warning("PAML output parsing failed: %s", result)
        return result

    from scipy.stats import chi2 as _chi2
    lrt  = max(-2.0 * (lnl0 - lnl2), 0.0)
    pval = float(_chi2.sf(lrt, df=1))
    result = {
        "status": "computed",
        "gene": gene_symbol,
        "omega_foreground": omega_fg,
        "omega_background": omega_bg,
        "acceleration_ratio": (
            round(float(omega_fg) / float(omega_bg), 6)
            if omega_fg is not None and omega_bg not in (None, 0) else None
        ),
        "lrt_statistic": round(lrt, 4),
        "lrt_chi2": round(lrt, 4),
        "lrt_pvalue": round(pval, 6),
        "alignment_length": len(next(iter(seqs.values()))) // 3 if seqs else 0,
        "species_count": len(seqs),
        "n_species": len(seqs),
        "foreground_label": foreground,
        "foreground_species": sorted(fg_present),
        "background_species": sorted(bg_present),
        "foreground_group": foreground,
        "newick": aligned.get("newick"),
        "provenance": {
            "paml": "codeml",
            "control_file_hash": hashlib.sha1(cache_key.encode()).hexdigest()[:12],
            "alignment_source": aligned.get("source", "ensembl_compara_genetree"),
            "tree_source": aligned.get("tree_source", "ensembl_compara_genetree"),
        },
    }
    if use_cache:
        _cache_set(cache_key, result)
    return result
