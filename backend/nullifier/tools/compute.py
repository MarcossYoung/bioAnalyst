"""Deterministic statistical compute layer for Nullifier v6.

Pure functions only — no LLM calls, no network, no file I/O. The Methodologist
(``agents/methodologist.py``) picks tests from ``TEST_LIBRARY``; the pipeline turns
gene data into a ``data`` dict (see ``tools/genomic_data.build_data``) and calls
``run_analysis_plan(plan, data)``. The Interpreter then reads the typed results.

The ``data`` dict shape the resolvers understand::

    data = {
      "groups":    {"<group>": {"<metric>": [float, ...], ...}, ...},
      "variables": {"<name>": [float, ...]},          # flat aligned vectors
      "gene_index": ["GENE1", "GENE2", ...],          # order for `variables`
      "tables":    {"<name>": [[int, ...], ...]},     # contingency tables
    }

An ``inputs`` reference may be a literal list/2-D list, a bare variable name, or a
dotted ``"group.metric"`` string.
"""
from __future__ import annotations

import math
import re
import statistics as _stats
from typing import Callable

import numpy as np
from scipy import stats as sps

from .phenotypes import ASSOCIATION_ONLY_GUARD, CORTICAL_NEURON_MIN_SPECIES, CORTICAL_NEURON_TRAIT

ALPHA = 0.05

TEST_RESULT_FIELDS = (
    "test",
    "requested",
    "available",
    "error",
    "n",
    "statistic",
    "p_value",
    "p_value_adjusted",
    "significant",
    "significant_adjusted",
    "effect_size",
    "effect_size_name",
    "effect_size_label",
    "ci",
    "ci_lower",
    "ci_upper",
    "method",
    "inputs",
    "details",
    "warnings",
    "rationale",
)

# Categories the tool genuinely cannot verify from Ensembl alone (moved from analyst.py).
NOT_VERIFIABLE_HERE = [
    "branch-specific / lineage-specific dN/dS (requires PAML/codeml on an alignment)",
    "gene constraint scores (pLI / LOEUF — requires gnomAD)",
    "custom statistical tests and their p-values (requires the raw study data)",
    "sample-size adequacy and power (requires the study design)",
    "expression / single-cell results (requires the relevant atlas, not Ensembl gene records)",
]


# ── helpers ─────────────────────────────────────────────────────────────────
def _clean(values) -> np.ndarray:
    if values is None:
        return np.asarray([], dtype=float)
    out = []
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        out.append(f)
    return np.asarray(out, dtype=float)


def _sign(x: float) -> int:
    return 0 if x == 0 else (1 if x > 0 else -1)


def _round(x, n=6):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, n)


def _ci_bounds(ci) -> tuple:
    if isinstance(ci, (list, tuple)) and len(ci) >= 2:
        return _round(ci[0]), _round(ci[1])
    return None, None


def _ci_unavailable(reason: str) -> dict:
    return {"value": None, "reason": reason}


def _bootstrap_two_sample_ci(a, b, fn: Callable, n_iter: int = 1000,
                             conf: float = 0.95, seed: int = 0):
    a, b = _clean(a), _clean(b)
    if len(a) < 2 or len(b) < 2:
        return _ci_unavailable("need >=2 values per group for bootstrap CI")
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(int(n_iter)):
        aa = rng.choice(a, size=len(a), replace=True)
        bb = rng.choice(b, size=len(b), replace=True)
        vals.append(fn(aa, bb))
    lo, hi = np.percentile(vals, [(1 - conf) / 2 * 100, (1 + conf) / 2 * 100])
    return [_round(lo), _round(hi)]


def _bootstrap_one_sample_ci(values, fn: Callable, n_iter: int = 1000,
                             conf: float = 0.95, seed: int = 0):
    a = _clean(values)
    if len(a) < 2:
        return _ci_unavailable("need >=2 values for bootstrap CI")
    rng = np.random.default_rng(seed)
    vals = [fn(rng.choice(a, size=len(a), replace=True)) for _ in range(int(n_iter))]
    lo, hi = np.percentile(vals, [(1 - conf) / 2 * 100, (1 + conf) / 2 * 100])
    return [_round(lo), _round(hi)]


def _cohens_d_value(a, b) -> float:
    a, b = _clean(a), _clean(b)
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return 0.0
    sp = math.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2))
    return float((a.mean() - b.mean()) / sp) if sp > 0 else 0.0


def _cliffs_delta_value(a, b) -> float:
    a, b = _clean(a), _clean(b)
    if len(a) == 0 or len(b) == 0:
        return 0.0
    gt = sum(1 for x in a for y in b if x > y)
    lt = sum(1 for x in a for y in b if x < y)
    return float((gt - lt) / (len(a) * len(b)))


def _test_result(
    test: str,
    *,
    requested: str | None = None,
    available: bool = True,
    error: str | None = None,
    n=None,
    statistic=None,
    p_value=None,
    p_value_adjusted=None,
    significant=None,
    significant_adjusted=None,
    effect_size=None,
    effect_size_name: str | None = None,
    effect_size_label: str | None = None,
    ci=None,
    method: str = "",
    inputs: dict | None = None,
    details: dict | None = None,
    warnings: list | None = None,
    rationale: str | None = None,
    **extra,
) -> dict:
    ci_lower, ci_upper = _ci_bounds(ci)
    label = effect_size_label or effect_size_name
    out = {
        "test": test,
        "requested": requested or test,
        "available": bool(available),
        "error": error,
        "n": n,
        "statistic": _round(statistic),
        "p_value": _round(p_value, 8),
        "p_value_adjusted": _round(p_value_adjusted, 8),
        "significant": significant,
        "significant_adjusted": significant_adjusted,
        "effect_size": _round(effect_size),
        "effect_size_name": effect_size_name,
        "effect_size_label": label,
        "ci": ci,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "method": method,
        "inputs": inputs or {},
        "details": details or {},
        "warnings": warnings or [],
        "rationale": rationale,
    }
    out.update(extra)
    return validate_test_result(out)


def validate_test_result(result: dict) -> dict:
    missing = [field for field in TEST_RESULT_FIELDS if field not in result]
    if missing:
        raise ValueError(f"Compute TestResult missing required field(s): {', '.join(missing)}")
    if not isinstance(result["test"], str) or not result["test"]:
        raise ValueError("Compute TestResult.test must be a non-empty string")
    if not isinstance(result["requested"], str) or not result["requested"]:
        raise ValueError("Compute TestResult.requested must be a non-empty string")
    if not isinstance(result["available"], bool):
        raise ValueError("Compute TestResult.available must be a boolean")
    if result["warnings"] is None:
        result["warnings"] = []
    if result["details"] is None:
        result["details"] = {}
    if result["inputs"] is None:
        result["inputs"] = {}
    return result


# ── effect sizes ────────────────────────────────────────────────────────────
def cohens_d(a, b) -> dict:
    a, b = _clean(a), _clean(b)
    if len(a) < 2 or len(b) < 2:
        return _test_result("cohens_d", error="need >=2 values per group",
                            n=[int(len(a)), int(len(b))])
    na, nb = len(a), len(b)
    sp = math.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2))
    d = (a.mean() - b.mean()) / sp if sp > 0 else 0.0
    ci = _bootstrap_two_sample_ci(a, b, _cohens_d_value)
    return _test_result("cohens_d", n=[int(na), int(nb)], effect_size=d,
                        effect_size_name="cohens_d", statistic=d, p_value=None,
                        ci=ci, significant=None, method="Cohen's d (pooled SD)")


def cliffs_delta(a, b) -> dict:
    a, b = _clean(a), _clean(b)
    if len(a) == 0 or len(b) == 0:
        return _test_result("cliffs_delta", error="empty group", n=[int(len(a)), int(len(b))])
    gt = sum(1 for x in a for y in b if x > y)
    lt = sum(1 for x in a for y in b if x < y)
    delta = (gt - lt) / (len(a) * len(b))
    mag = abs(delta)
    label = ("negligible" if mag < 0.147 else "small" if mag < 0.33
             else "medium" if mag < 0.474 else "large")
    ci = _bootstrap_two_sample_ci(a, b, _cliffs_delta_value)
    return _test_result("cliffs_delta", n=[int(len(a)), int(len(b))],
                        effect_size=delta, effect_size_name="cliffs_delta",
                        effect_size_label=label, statistic=delta, magnitude=label,
                        p_value=None, ci=ci, significant=None, method="Cliff's delta")


# ── group-difference tests ──────────────────────────────────────────────────
def kruskal_wallis(groups: dict) -> dict:
    labels = list(groups)
    arrays = [(k, _clean(groups[k])) for k in labels]
    non_empty = [(k, a) for k, a in arrays if len(a) > 0]
    sizes = {k: int(len(a)) for k, a in arrays}
    if len(non_empty) < 2 or any(len(a) < 2 for _, a in non_empty):
        return _test_result("kruskal_wallis", error="need >=2 groups with >=2 values each",
                            inputs={"group_sizes": sizes})
    H, p = sps.kruskal(*[a for _, a in non_empty])
    k = len(non_empty)
    n = sum(len(a) for _, a in non_empty)
    eps2 = (H - k + 1) / (n - k) if n > k else None  # epsilon-squared
    return _test_result(
        "kruskal_wallis",
        n=int(n),
        inputs={"groups": [k for k, _ in non_empty], "group_sizes": sizes},
        statistic=H,
        df=k - 1,
        p_value=p,
        significant=bool(p < ALPHA),
        effect_size=eps2,
        effect_size_name="epsilon_squared",
        ci=_ci_unavailable("epsilon-squared CI is not computed by this backend"),
        method=f"Kruskal-Wallis H-test across {k} groups (scipy.stats.kruskal)",
    )


def mann_whitney_posthoc(groups: dict, correction: str = "fdr_bh") -> dict:
    labels = [k for k in groups if len(_clean(groups[k])) > 0]
    pairs = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = _clean(groups[labels[i]]), _clean(groups[labels[j]])
            if len(a) < 1 or len(b) < 1:
                continue
            try:
                U, p = sps.mannwhitneyu(a, b, alternative="two-sided")
            except ValueError as e:
                pairs.append({"pair": [labels[i], labels[j]], "error": str(e)})
                continue
            cd = cliffs_delta(a, b)
            pairs.append({
                "pair": [labels[i], labels[j]], "n": [int(len(a)), int(len(b))],
                "statistic": _round(U), "p_value": _round(p, 8),
                "median_diff": _round(float(np.median(a) - np.median(b))),
                "effect_size": cd.get("effect_size"), "effect_size_name": "cliffs_delta",
                "ci": cd.get("ci"), "ci_lower": cd.get("ci_lower"), "ci_upper": cd.get("ci_upper"),
                "significant": bool(p < ALPHA),
            })
    p_pairs = [(idx, pair["p_value"]) for idx, pair in enumerate(pairs)
               if isinstance(pair.get("p_value"), (int, float))]
    corr_key = _CORRECTION_ALIASES.get(correction, correction)
    correction_summary = None
    if corr_key and p_pairs:
        corr = _correction([p for _, p in p_pairs], corr_key)
        for (idx, _), p_adj, reject in zip(p_pairs, corr["pvals_adjusted"], corr["reject"]):
            pairs[idx]["p_value_adjusted"] = p_adj
            pairs[idx]["significant_adjusted"] = bool(reject)
        correction_summary = {"method": corr_key, "n_tests": len(p_pairs), "alpha": ALPHA}
    return _test_result(
        "mann_whitney_posthoc",
        n=sum(sum(pair.get("n", [])) for pair in pairs if isinstance(pair.get("n"), list)) or None,
        effect_size_name="cliffs_delta",
        ci=_ci_unavailable("pairwise CIs are reported in details.pairs"),
        method="Pairwise Mann-Whitney U with Cliff's delta",
        details={"pairs": pairs, "correction": correction_summary},
        warnings=["Pairwise p-values and effect sizes are reported in details.pairs."],
        pairs=pairs,
    )


# ── correlation ─────────────────────────────────────────────────────────────
def _paired(x, y) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    for a, b in zip(x if x is not None else [], y if y is not None else []):
        if a is None or b is None:
            continue
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if any(map(lambda v: math.isnan(v) or math.isinf(v), (fa, fb))):
            continue
        xs.append(fa); ys.append(fb)
    return np.asarray(xs), np.asarray(ys)


def _paired_with_diagnostics(x, y) -> tuple[np.ndarray, np.ndarray, dict]:
    x_raw = list(x if x is not None else [])
    y_raw = list(y if y is not None else [])
    xs, ys = _paired(x_raw, y_raw)
    return xs, ys, {
        "x_length": len(x_raw),
        "y_length": len(y_raw),
        "paired_n": int(len(xs)),
        "dropped_n": max(len(x_raw), len(y_raw)) - int(len(xs)),
    }


def spearman(x, y) -> dict:
    x, y, diag = _paired_with_diagnostics(x, y)
    if diag["x_length"] != diag["y_length"]:
        return _test_result(
            "spearman", available=False, skipped=True,
            skip_reason="x and y have different lengths",
            n=int(len(x)), details=diag, ci=_ci_unavailable("test skipped"),
        )
    if len(x) < 3:
        return _test_result(
            "spearman", available=False, skipped=True,
            skip_reason="need >=3 paired observations",
            n=int(len(x)), details=diag, ci=_ci_unavailable("test skipped"),
        )
    if len(set(x.tolist())) < 2 or len(set(y.tolist())) < 2:
        return _test_result(
            "spearman", available=False, skipped=True,
            skip_reason="correlation undefined for constant input",
            n=int(len(x)), details=diag, ci=_ci_unavailable("test skipped"),
        )
    rho, p = sps.spearmanr(x, y)
    if math.isnan(float(rho)) or math.isnan(float(p)):
        return _test_result(
            "spearman", available=False, skipped=True,
            skip_reason="correlation undefined for constant input",
            n=int(len(x)), details=diag, ci=_ci_unavailable("test skipped"),
        )
    return _test_result("spearman", n=int(len(x)), statistic=rho,
                        effect_size=rho, effect_size_name="rho", p_value=p,
                        ci=_fisher_ci(rho, len(x)), significant=bool(p < ALPHA),
                        method="Spearman rank correlation (scipy.stats.spearmanr)")


def pearson(x, y) -> dict:
    x, y, diag = _paired_with_diagnostics(x, y)
    if diag["x_length"] != diag["y_length"]:
        return _test_result(
            "pearson", available=False, skipped=True,
            skip_reason="x and y have different lengths",
            n=int(len(x)), details=diag, ci=_ci_unavailable("test skipped"),
        )
    if len(x) < 3:
        return _test_result(
            "pearson", available=False, skipped=True,
            skip_reason="need >=3 paired observations",
            n=int(len(x)), details=diag, ci=_ci_unavailable("test skipped"),
        )
    r, p = sps.pearsonr(x, y)
    return _test_result("pearson", n=int(len(x)), statistic=r,
                        effect_size=r, effect_size_name="r", p_value=p,
                        ci=_fisher_ci(r, len(x)), significant=bool(p < ALPHA),
                        method="Pearson product-moment correlation (scipy.stats.pearsonr)")


def _fisher_ci(r: float, n: int, conf: float = 0.95):
    if n < 4 or abs(r) >= 1:
        return None
    z = math.atanh(r)
    se = 1 / math.sqrt(n - 3)
    crit = sps.norm.ppf(1 - (1 - conf) / 2)
    lo, hi = math.tanh(z - crit * se), math.tanh(z + crit * se)
    return [_round(lo), _round(hi)]


# ── contingency ─────────────────────────────────────────────────────────────
def _table(table):
    arr = np.asarray(table, dtype=float)
    if arr.ndim != 2:
        raise ValueError("contingency table must be 2-D")
    return arr


def fisher_exact(table) -> dict:
    arr = _table(table)
    if arr.shape != (2, 2):
        return _test_result("fisher_exact", error="Fisher's exact requires a 2x2 table",
                            inputs={"shape": list(arr.shape)}, shape=list(arr.shape))
    odds, p = sps.fisher_exact(arr, alternative="two-sided")
    return _test_result("fisher_exact", n=int(arr.sum()), inputs={"table": arr.tolist()},
                        table=arr.tolist(), statistic=odds, effect_size=odds,
                        effect_size_name="odds_ratio", p_value=p,
                        ci=_ci_unavailable("Fisher exact CI is not computed by this backend"),
                        significant=bool(p < ALPHA), method="Fisher's exact test (2x2)")


def chi_square(table) -> dict:
    arr = _table(table)
    if arr.size == 0 or (arr.sum(axis=1) == 0).any() or (arr.sum(axis=0) == 0).any():
        return _test_result("chi_square", error="table has an empty row or column",
                            inputs={"table": arr.tolist()}, table=arr.tolist())
    chi2, p, dof, expected = sps.chi2_contingency(arr)
    n = arr.sum()
    k = min(arr.shape) - 1
    cramers_v = math.sqrt(chi2 / (n * k)) if n > 0 and k > 0 else None
    low_expected = bool((np.asarray(expected) < 5).any())
    return _test_result("chi_square", n=int(n), inputs={"table": arr.tolist()}, table=arr.tolist(),
                        statistic=chi2, df=int(dof), p_value=p, effect_size=cramers_v,
                        effect_size_name="cramers_v",
                        ci=_ci_unavailable("Cramer's V CI is not computed by this backend"),
                        significant=bool(p < ALPHA),
                        low_expected_counts=low_expected,
                        method="Pearson chi-square test of independence (scipy.stats.chi2_contingency)")


# ── resampling ──────────────────────────────────────────────────────────────
_STAT_FNS: dict[str, Callable] = {
    "mean": np.mean, "median": np.median, "std": lambda a: np.std(a, ddof=1),
}


def bootstrap_ci(values, statistic: str = "mean", n_iter: int = 5000, conf: float = 0.95,
                 seed: int = 0) -> dict:
    a = _clean(values)
    if len(a) < 2:
        return _test_result("bootstrap_ci", error="need >=2 values", n=int(len(a)))
    fn = _STAT_FNS.get(statistic, np.mean)
    rng = np.random.default_rng(seed)
    boot = np.array([fn(rng.choice(a, size=len(a), replace=True)) for _ in range(int(n_iter))])
    lo, hi = np.percentile(boot, [(1 - conf) / 2 * 100, (1 + conf) / 2 * 100])
    return _test_result("bootstrap_ci", n=int(len(a)), statistic_name=statistic,
                        statistic=float(fn(a)), ci=[_round(lo), _round(hi)], p_value=None,
                        n_iter=int(n_iter), conf=conf, significant=None,
                        method=f"Percentile bootstrap {int(conf*100)}% CI for the {statistic} ({int(n_iter)} resamples)")


def permutation_test(a, b, statistic: str = "mean_diff", n_iter: int = 10000, seed: int = 0) -> dict:
    a, b = _clean(a), _clean(b)
    if len(a) < 2 or len(b) < 2:
        return _test_result("permutation_test", error="need >=2 values per group",
                            n=[int(len(a)), int(len(b))])
    if statistic == "median_diff":
        stat_fn = lambda x, y: np.median(x) - np.median(y)
    else:
        stat_fn = lambda x, y: np.mean(x) - np.mean(y)
    obs = stat_fn(a, b)
    rng = np.random.default_rng(seed)
    pooled = np.concatenate([a, b])
    na = len(a)
    count = 0
    for _ in range(int(n_iter)):
        rng.shuffle(pooled)
        if abs(stat_fn(pooled[:na], pooled[na:])) >= abs(obs):
            count += 1
    p = (count + 1) / (int(n_iter) + 1)
    return _test_result("permutation_test", n=[int(len(a)), int(len(b))], statistic_name=statistic,
                        statistic=float(obs), p_value=p, n_iter=int(n_iter),
                        effect_size=float(obs), effect_size_name=statistic,
                        ci=_bootstrap_two_sample_ci(a, b, stat_fn),
                        significant=bool(p < ALPHA),
                        method=f"Two-sided permutation test on the {statistic} ({int(n_iter)} permutations)")


# ── multiple-testing corrections (manual; no statsmodels dependency) ────────
def _adjust(pvals, method: str):
    p = np.asarray([float(x) for x in pvals], dtype=float)
    n = len(p)
    if n == 0:
        return np.array([]), np.array([], dtype=bool)
    if method == "bonferroni":
        adj = np.minimum(p * n, 1.0)
    elif method == "holm":
        order = np.argsort(p)
        adj_sorted = np.empty(n)
        running = 0.0
        for rank, idx in enumerate(order):
            running = max(running, (n - rank) * p[idx])
            adj_sorted[rank] = min(running, 1.0)
        adj = np.empty(n)
        adj[order] = adj_sorted
    else:  # fdr_bh (Benjamini–Hochberg)
        order = np.argsort(p)
        ranks = np.arange(1, n + 1)
        adj_sorted = p[order] * n / ranks
        # enforce monotonicity from the largest p downward
        adj_sorted = np.minimum.accumulate(adj_sorted[::-1])[::-1]
        adj_sorted = np.minimum(adj_sorted, 1.0)
        adj = np.empty(n)
        adj[order] = adj_sorted
    return adj, adj <= ALPHA


def _correction(pvals, method: str) -> dict:
    adj, reject = _adjust(pvals, method)
    return {"method": method, "n_tests": len(adj),
            "pvals_adjusted": [_round(float(x), 8) for x in adj],
            "reject": [bool(r) for r in reject]}


def bonferroni(pvals) -> dict:
    return _correction(pvals, "bonferroni")


def benjamini_hochberg(pvals) -> dict:
    return _correction(pvals, "fdr_bh")


_CORRECTION_ALIASES = {
    "bonferroni": "bonferroni", "holm": "holm",
    "benjamini_hochberg": "fdr_bh", "fdr_bh": "fdr_bh", "bh": "fdr_bh", "fdr": "fdr_bh",
    "none": None, "": None, None: None,
}


def _paml_branch_model(inputs: dict, data: dict) -> dict:
    paml = data.get("paml") or {}
    computed = [v for v in paml.values()
                if isinstance(v, dict) and v.get("status") == "computed"]
    if not computed:
        status_counts: dict[str, int] = {}
        for result in paml.values():
            if isinstance(result, dict):
                status = result.get("status") or "unknown"
                status_counts[status] = status_counts.get(status, 0) + 1
        return _test_result(
            "paml_branch_model",
            available=False,
            error="no computed PAML results",
            details={"paml_status_counts": status_counts},
            closest_alternative="Compara pairwise dN/dS (already computed)",
            method="PAML codeml branch model 2 LRT",
        )
    best = min(computed, key=lambda x: x.get("lrt_pvalue", 1.0))
    return {
        **_test_result(
            "paml_branch_model",
            available=True,
            n=len(computed),
            statistic=best.get("lrt_statistic", best.get("lrt_chi2")),
            p_value=best["lrt_pvalue"],
            significant=best["lrt_pvalue"] < 0.05,
            effect_size=best.get("omega_foreground"),
            effect_size_name="omega_foreground",
            effect_size_label=(
            "positive selection"
            if (best.get("omega_foreground") or 0) > 1 else "purifying/neutral"
            ),
            ci=_ci_unavailable("PAML branch model CI is not computed"),
            method="PAML codeml branch model 2 LRT",
        ),
        "per_gene": paml,
        "foreground_group": inputs.get("foreground", "primates"),
        "details": {
            "best_gene": best.get("gene"),
            "omega_foreground": best.get("omega_foreground"),
            "omega_background": best.get("omega_background"),
            "acceleration_ratio": best.get("acceleration_ratio"),
            "computed_genes": len(computed),
        },
    }


# ── test library / plan dispatch ────────────────────────────────────────────
# name -> (callable, list-of-input-keys, group-or-pair-test?)
TEST_LIBRARY: dict[str, dict] = {
    "kruskal_wallis":      {"fn": kruskal_wallis,      "kind": "groups", "constructs": {"set_difference"}},
    "mann_whitney_posthoc": {"fn": mann_whitney_posthoc, "kind": "groups", "constructs": {"set_difference"}},
    "spearman":            {"fn": spearman,            "kind": "xy", "constructs": {"set_difference"}},
    "pearson":             {"fn": pearson,             "kind": "xy", "constructs": {"set_difference"}},
    "fisher_exact":        {"fn": fisher_exact,        "kind": "table", "constructs": {"set_difference"}},
    "chi_square":          {"fn": chi_square,          "kind": "table", "constructs": {"set_difference"}},
    "bootstrap_ci":        {"fn": bootstrap_ci,        "kind": "values", "constructs": {"set_difference"}},
    "permutation_test":    {"fn": permutation_test,    "kind": "ab", "constructs": {"set_difference"}},
    "cliffs_delta":        {"fn": cliffs_delta,        "kind": "ab", "constructs": {"set_difference"}},
    "cohens_d":            {"fn": cohens_d,            "kind": "ab", "constructs": {"set_difference"}},
    "paml_branch_model":   {"fn": _paml_branch_model,  "kind": "paml", "constructs": {"lineage_specific_selection"}},
}

TEST_LIBRARY_DOC = """Available tests (request by name; inputs reference the supplied data dict):
- kruskal_wallis        inputs: {"metric": "<metric>", "groups": ["<group>", ...]}
- mann_whitney_posthoc  inputs: {"metric": "<metric>", "groups": ["<group>", ...]}  (pairwise; use a correction)
- spearman / pearson    inputs: {"x": "<var or group.metric>", "y": "<var or group.metric>"}
- fisher_exact          inputs: {"table": "<table name or 2x2 literal>"}
- chi_square            inputs: {"table": "<table name or RxC literal>"}
- bootstrap_ci          inputs: {"values": "<var or group.metric>", "statistic": "mean|median|std"}
- permutation_test      inputs: {"a": "<var or group.metric>", "b": "<var or group.metric>", "statistic": "mean_diff|median_diff"}
- cliffs_delta / cohens_d  inputs: {"a": "<...>", "b": "<...>"}
- paml_branch_model  inputs: {"foreground": "primates"|"rodents"|"human"}.
  Use when hypothesis involves lineage-specific acceleration or purifying selection.
  Degrades gracefully (available=False) when codeml binary is not installed.
- mirrortree_lite inputs: {"set_a": "starter", "set_b": "expanded.<name>", "background": "background.random_300"}.
  Use for cross_lineage_rate_correlation claims; reads data["rate_vectors"] and compares
  background-residualized cross-set per-lineage dN/dS covariation against a random-background null.
- erc inputs: {"set_a": "starter", "set_b": "expanded.<name>", "controls": ["controls.<name>"], "background": "background.random_300"}.
  Primary Stage-3 cross_lineage_rate_correlation test; reads branch-rate vectors when available,
  compares background-residualized set-mean branch rates, and permutes matched controls.
- rerconverge inputs: {"sets": ["starter", "expanded.<name>"], "controls": ["controls.<name>"], "trait": "cortical_neurons"}.
  Secondary/exploratory Stage-4 phenotype-association test; reads precomputed container results,
  requires >=20 species with cortical-neuron counts, and never overrides ERC.
- Pairwise dN/dS is available as metric/variable "dnds" when homology alignments
  and CDS pass the NG86 estimator gates. For coordinated-rate hypotheses, request spearman with
  {"x": "dnds", "y": "<other aligned variable>"} or group tests with metric "dnds".
- Branch-model omega distribution tests use metric "omega_foreground" or
  "acceleration_ratio" (Kruskal-Wallis, Mann-Whitney posthoc, Spearman).
Corrections: "benjamini_hochberg" (default for multi-test families), "bonferroni", "holm", "none".""" 


def _mean_ignore_none(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return float(np.mean(clean)) if clean else None


def residualize_rate_vectors(rate_vectors: dict, background_set: str = "background.random_300") -> dict:
    """Subtract per-lineage background means from each gene's rate vector."""
    panel = list((rate_vectors or {}).get("panel") or [])
    rates = (rate_vectors or {}).get("rates") or {}
    sets = (rate_vectors or {}).get("sets") or {}
    background_genes = [g for g in sets.get(background_set, []) if g in rates]
    background_means = []
    for i in range(len(panel)):
        background_means.append(_mean_ignore_none([rates[g][i] for g in background_genes if i < len(rates[g])]))

    residuals: dict[str, list[float | None]] = {}
    for gene, vector in rates.items():
        residuals[gene] = [
            (float(v) - background_means[i])
            if v is not None and i < len(background_means) and background_means[i] is not None
            else None
            for i, v in enumerate(vector)
        ]
    return {
        "panel": panel,
        "sets": sets,
        "rates": residuals,
        "background_means": background_means,
        "background_set": background_set,
        "background_gene_count": len(background_genes),
    }


def _vector_corr(a: list, b: list, min_shared: int = 5) -> tuple[float | None, int]:
    xs, ys = [], []
    for x, y in zip(a or [], b or []):
        if x is None or y is None:
            continue
        try:
            fx, fy = float(x), float(y)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fx) and math.isfinite(fy):
            xs.append(fx)
            ys.append(fy)
    if len(xs) < min_shared or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None, len(xs)
    r = float(np.corrcoef(np.asarray(xs), np.asarray(ys))[0, 1])
    return (r, len(xs)) if math.isfinite(r) else (None, len(xs))


def pairwise_rate_covariation(
    residualized: dict,
    genes_a: list[str],
    genes_b: list[str],
    *,
    min_shared: int = 5,
    exclude_self: bool = True,
) -> list[dict]:
    rates = residualized.get("rates") or {}
    pairs = []
    for ga in genes_a or []:
        if ga not in rates:
            continue
        for gb in genes_b or []:
            if gb not in rates or (exclude_self and ga == gb):
                continue
            r, n = _vector_corr(rates[ga], rates[gb], min_shared=min_shared)
            if r is not None:
                pairs.append({"gene_a": ga, "gene_b": gb, "r": r, "shared_species": n})
    return pairs


def _mean_pair_r(pairs: list[dict]) -> float | None:
    vals = [p["r"] for p in pairs if isinstance(p.get("r"), (int, float))]
    return float(np.mean(vals)) if vals else None


def _select_default_set(sets: dict, prefix: str) -> str | None:
    names = [name for name in sets if name.startswith(prefix)]
    if not names:
        return None
    bbb = [name for name in names if "bbb" in name]
    return sorted(bbb or names)[0]


def _usable_set_failure(rate_vectors: dict, names: list[str]) -> tuple[str, dict] | None:
    set_usability = (rate_vectors or {}).get("set_usability") or {}
    degraded = [
        (name, set_usability.get(name) or {})
        for name in names
        if name in set_usability and not (set_usability.get(name) or {}).get("usable", True)
    ]
    if not degraded:
        return None
    reason = "; ".join(
        f"set {name} degraded: too few genes survive FP-risk filter"
        if meta.get("risk_degraded")
        else f"{name}: {meta.get('reason') or 'degraded rate coverage'}"
        for name, meta in degraded
    )
    return reason, {name: meta for name, meta in degraded}


def _mean_rate_vector(residualized: dict, genes: list[str]) -> tuple[list[float | None], int]:
    rates = residualized.get("rates") or {}
    panel = residualized.get("panel") or []
    out: list[float | None] = []
    contributors = set()
    for i in range(len(panel)):
        vals = []
        for gene in genes or []:
            vector = rates.get(gene)
            if not vector or i >= len(vector):
                continue
            value = vector[i]
            if value is None:
                continue
            try:
                f = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f):
                vals.append(f)
                contributors.add(gene)
        out.append(float(np.mean(vals)) if vals else None)
    return out, len(contributors)


def _bootstrap_erc_ci(
    residualized: dict,
    genes_a: list[str],
    genes_b: list[str],
    *,
    min_shared: int,
    n_iter: int = 500,
    seed: int = 0,
) -> list[float] | dict:
    if len(genes_a) < 2 or len(genes_b) < 2:
        return _ci_unavailable("need >=2 genes per set for ERC bootstrap CI")
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_iter):
        sample_a = rng.choice(genes_a, size=len(genes_a), replace=True).tolist()
        sample_b = rng.choice(genes_b, size=len(genes_b), replace=True).tolist()
        mean_a, _ = _mean_rate_vector(residualized, sample_a)
        mean_b, _ = _mean_rate_vector(residualized, sample_b)
        r, _ = _vector_corr(mean_a, mean_b, min_shared=min_shared)
        if r is not None:
            vals.append(r)
    if not vals:
        return _ci_unavailable("bootstrap resamples had insufficient shared branches")
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return [_round(lo), _round(hi)]


def erc(rate_vectors: dict, inputs: dict | None = None) -> dict:
    inputs = inputs or {}
    sets = (rate_vectors or {}).get("sets") or {}
    set_a_name = inputs.get("set_a") or "starter"
    set_b_name = inputs.get("set_b") or _select_default_set(sets, "expanded.")
    background_name = inputs.get("background") or "background.random_300"
    controls_in = inputs.get("controls")
    control_names = (
        [str(c) for c in controls_in]
        if isinstance(controls_in, (list, tuple))
        else ([str(controls_in)] if isinstance(controls_in, str) else sorted(k for k in sets if k.startswith("controls.")))
    )
    min_shared = int(inputs.get("min_shared_branches") or inputs.get("min_shared_species") or 5)
    n_iter = int(inputs.get("n_iter") or 2000)
    seed = int(inputs.get("seed") or 0)

    if not rate_vectors or not sets:
        return _test_result("erc", available=False, skipped=True,
                            skip_reason="rate_vectors data unavailable",
                            ci=_ci_unavailable("test skipped"))
    missing = [name for name in [set_a_name, set_b_name] if not name or name not in sets]
    if missing:
        return _test_result(
            "erc", available=False, skipped=True,
            skip_reason="set_a or set_b not available in rate_vectors",
            details={"set_a": set_a_name, "set_b": set_b_name, "available_sets": sorted(sets)},
            ci=_ci_unavailable("test skipped"),
        )
    control_names = [name for name in control_names if name in sets]
    if not control_names:
        return _test_result(
            "erc", available=False, skipped=True,
            skip_reason="matched control sets not available in rate_vectors",
            details={"available_sets": sorted(sets)},
            ci=_ci_unavailable("test skipped"),
        )

    degraded = _usable_set_failure(rate_vectors, [set_a_name, set_b_name] + control_names)
    if degraded:
        reason, meta = degraded
        return _test_result(
            "erc", available=False, skipped=True,
            skip_reason=f"ERC refused because {reason}",
            details={"set_usability": meta},
            ci=_ci_unavailable("test skipped"),
        )

    residualized = residualize_rate_vectors(rate_vectors, background_name) if background_name in sets else {
        "panel": list((rate_vectors or {}).get("panel") or []),
        "sets": sets,
        "rates": (rate_vectors or {}).get("rates") or {},
        "background_means": [],
        "background_set": None,
        "background_gene_count": 0,
    }
    set_a = [g for g in sets.get(set_a_name, []) if g in (residualized.get("rates") or {})]
    set_b = [g for g in sets.get(set_b_name, []) if g in (residualized.get("rates") or {})]
    controls = [g for name in control_names for g in sets.get(name, []) if g in (residualized.get("rates") or {})]

    mean_a, contributors_a = _mean_rate_vector(residualized, set_a)
    mean_b, contributors_b = _mean_rate_vector(residualized, set_b)
    observed_r, shared = _vector_corr(mean_a, mean_b, min_shared=min_shared)
    if observed_r is None:
        return _test_result(
            "erc", available=False, skipped=True,
            skip_reason="set-mean branch-rate vectors had too few shared non-constant branches",
            details={
                "set_a": set_a_name,
                "set_b": set_b_name,
                "contributors": {"set_a": contributors_a, "set_b": contributors_b},
                "shared_branches": shared,
                "min_shared_branches": min_shared,
            },
            ci=_ci_unavailable("test skipped"),
        )
    if len(controls) < max(1, len(set_b)):
        return _test_result(
            "erc", available=False, skipped=True,
            skip_reason="not enough matched-control genes for ERC permutation",
            details={"control_gene_count": len(controls), "required": len(set_b), "control_sets": control_names},
            ci=_ci_unavailable("test skipped"),
        )

    rng = np.random.default_rng(seed)
    null_values = []
    sample_size = max(1, len(set_b))
    for _ in range(n_iter):
        sample_b = rng.choice(controls, size=sample_size, replace=False).tolist()
        mean_ctrl, _ = _mean_rate_vector(residualized, sample_b)
        r, _ = _vector_corr(mean_a, mean_ctrl, min_shared=min_shared)
        if r is not None:
            null_values.append(r)
    p_value = (
        (sum(1 for v in null_values if abs(v) >= abs(observed_r)) + 1) / (len(null_values) + 1)
        if null_values else None
    )
    null_mean = float(np.mean(null_values)) if null_values else None
    effect = observed_r - null_mean if null_mean is not None else observed_r
    ci = _bootstrap_erc_ci(
        residualized,
        set_a,
        set_b,
        min_shared=min_shared,
        n_iter=min(500, n_iter),
        seed=seed + 1,
    )
    provenance = (rate_vectors or {}).get("provenance") or {}
    warnings = []
    if provenance.get("source") != "iqtree_fixed_topology_relative_branch_rates":
        warnings.append("ERC is running on fallback rate vectors; Stage-3 branch-rate estimates were not available.")
    if background_name not in sets:
        warnings.append("No background set was available for residualization; ERC used raw relative rates.")

    return _test_result(
        "erc",
        n=int(shared),
        statistic=observed_r,
        p_value=p_value,
        significant=bool(p_value is not None and p_value < ALPHA),
        effect_size=effect,
        effect_size_name="erc_r_minus_matched_control_mean_r",
        ci=ci,
        method="ERC: Pearson correlation of background-residualized set-mean per-branch relative rates with matched-control permutation",
        inputs={
            "set_a": set_a_name,
            "set_b": set_b_name,
            "controls": control_names,
            "background": background_name if background_name in sets else None,
            "min_shared_branches": min_shared,
            "n_iter": n_iter,
            "seed": seed,
        },
        details={
            "panel": residualized.get("panel"),
            "shared_branches": shared,
            "contributors": {"set_a": contributors_a, "set_b": contributors_b},
            "background_gene_count": residualized.get("background_gene_count"),
            "control_gene_count": len(controls),
            "null_n": len(null_values),
            "null_mean": null_mean,
            "null_p025": float(np.percentile(null_values, 2.5)) if null_values else None,
            "null_p975": float(np.percentile(null_values, 97.5)) if null_values else None,
            "set_sizes": {"set_a": len(set_a), "set_b": len(set_b), "controls": len(controls)},
            "rate_vector_source": provenance.get("source"),
        },
        warnings=warnings,
    )


def mirrortree_lite(rate_vectors: dict, inputs: dict | None = None) -> dict:
    inputs = inputs or {}
    sets = (rate_vectors or {}).get("sets") or {}
    set_a_name = inputs.get("set_a") or "starter"
    set_b_name = inputs.get("set_b") or _select_default_set(sets, "expanded.")
    background_name = inputs.get("background") or "background.random_300"
    min_shared = int(inputs.get("min_shared_species") or 5)
    n_iter = int(inputs.get("n_iter") or 2000)
    seed = int(inputs.get("seed") or 0)

    if not rate_vectors or not sets:
        return _test_result("mirrortree_lite", available=False, skipped=True,
                            skip_reason="rate_vectors data unavailable",
                            ci=_ci_unavailable("test skipped"))
    if set_a_name not in sets or not set_b_name or set_b_name not in sets:
        return _test_result(
            "mirrortree_lite", available=False, skipped=True,
            skip_reason="set_a or set_b not available in rate_vectors",
            details={"set_a": set_a_name, "set_b": set_b_name, "available_sets": sorted(sets)},
            ci=_ci_unavailable("test skipped"),
        )
    if background_name not in sets:
        return _test_result(
            "mirrortree_lite", available=False, skipped=True,
            skip_reason="background set not available in rate_vectors",
            details={"background": background_name, "available_sets": sorted(sets)},
            ci=_ci_unavailable("test skipped"),
        )

    degraded = _usable_set_failure(rate_vectors, [set_a_name, set_b_name])
    if degraded:
        reason, meta = degraded
        return _test_result(
            "mirrortree_lite", available=False, skipped=True,
            skip_reason=f"cross-set comparison refused because {reason}",
            details={"set_usability": meta},
            ci=_ci_unavailable("test skipped"),
        )

    residualized = residualize_rate_vectors(rate_vectors, background_name)
    set_a = list(sets.get(set_a_name) or [])
    set_b = list(sets.get(set_b_name) or [])
    background = list(sets.get(background_name) or [])

    cross_pairs = pairwise_rate_covariation(residualized, set_a, set_b, min_shared=min_shared)
    cross_mean = _mean_pair_r(cross_pairs)
    if cross_mean is None:
        return _test_result(
            "mirrortree_lite", available=False, skipped=True,
            skip_reason="no cross-set gene pairs had enough shared species",
            details={"set_a": set_a_name, "set_b": set_b_name, "min_shared_species": min_shared},
            ci=_ci_unavailable("test skipped"),
        )

    within_a = pairwise_rate_covariation(residualized, set_a, set_a, min_shared=min_shared)
    within_b = pairwise_rate_covariation(residualized, set_b, set_b, min_shared=min_shared)
    a_bg = pairwise_rate_covariation(residualized, set_a, background, min_shared=min_shared)
    b_bg = pairwise_rate_covariation(residualized, set_b, background, min_shared=min_shared)

    rng = np.random.default_rng(seed)
    null_values = []
    bg_pool = [g for g in background if g in (residualized.get("rates") or {})]
    sample_size = max(1, min(len(set_b), len(bg_pool)))
    if len(bg_pool) >= sample_size:
        for _ in range(n_iter):
            sample_b = rng.choice(bg_pool, size=sample_size, replace=False).tolist()
            pairs = pairwise_rate_covariation(residualized, set_a, sample_b, min_shared=min_shared)
            m = _mean_pair_r(pairs)
            if m is not None:
                null_values.append(m)
    p_value = (sum(1 for v in null_values if v >= cross_mean) + 1) / (len(null_values) + 1) if null_values else None

    bg_means = [v for v in (_mean_pair_r(a_bg), _mean_pair_r(b_bg)) if v is not None]
    background_mean = float(np.mean(bg_means)) if bg_means else None
    effect = cross_mean - background_mean if background_mean is not None else cross_mean
    return _test_result(
        "mirrortree_lite",
        n=len(cross_pairs),
        statistic=cross_mean,
        p_value=p_value,
        significant=bool(p_value is not None and p_value < ALPHA),
        effect_size=effect,
        effect_size_name="cross_minus_background_mean_r",
        ci=_ci_unavailable("permutation null distribution reported in details"),
        method="Mirrortree-lite: Pearson correlation of background-residualized per-lineage dN/dS vectors",
        inputs={
            "set_a": set_a_name,
            "set_b": set_b_name,
            "background": background_name,
            "min_shared_species": min_shared,
            "n_iter": n_iter,
            "seed": seed,
        },
        details={
            "panel": residualized.get("panel"),
            "background_gene_count": residualized.get("background_gene_count"),
            "cross_pair_count": len(cross_pairs),
            "within_a_mean_r": _mean_pair_r(within_a),
            "within_b_mean_r": _mean_pair_r(within_b),
            "set_a_vs_background_mean_r": _mean_pair_r(a_bg),
            "set_b_vs_background_mean_r": _mean_pair_r(b_bg),
            "background_mean_r": background_mean,
            "null_n": len(null_values),
            "null_mean": float(np.mean(null_values)) if null_values else None,
            "null_p95": float(np.percentile(null_values, 95)) if null_values else None,
            "set_sizes": {"set_a": len(set_a), "set_b": len(set_b), "background": len(background)},
        },
        warnings=["Tier-1 mirrortree-lite uses pairwise human-referenced dN/dS vectors; full ERC is deferred."],
    )


def rerconverge_test(inputs: dict | None, data: dict | None = None) -> dict:
    """Summarize precomputed RERconverge container results as a secondary test."""
    inputs = inputs or {}
    data = data or {}
    trait_name = inputs.get("trait") or CORTICAL_NEURON_TRAIT
    min_species = int(inputs.get("min_species") or CORTICAL_NEURON_MIN_SPECIES)
    trait_axis = ((data.get("phenotypes") or {}).get(trait_name) or {})
    requested_sets = [
        str(s) for s in (inputs.get("sets") or inputs.get("test_sets") or ["starter"])
        if s
    ]
    control_names = [
        str(s) for s in (inputs.get("controls") or [])
        if s
    ]

    if not trait_axis:
        return _test_result(
            "rerconverge",
            available=False,
            skipped=True,
            skip_reason=f"phenotype axis '{trait_name}' unavailable",
            method="RERconverge rate-phenotype correlation",
            inputs={"sets": requested_sets, "controls": control_names, "trait": trait_name, "min_species": min_species},
            details={"secondary": True, "primary_test": "erc", "overclaim_guard": ASSOCIATION_ONLY_GUARD},
            warnings=[ASSOCIATION_ONLY_GUARD, "RERconverge is secondary; ERC carries the coordinated-rate verdict."],
            ci=_ci_unavailable("test skipped"),
        )
    if bool(trait_axis.get("underpowered")) or int(trait_axis.get("usable_species") or 0) < min_species:
        return _test_result(
            "rerconverge",
            available=False,
            skipped=True,
            skip_reason=trait_axis.get("reason") or f"need >= {min_species} species with cortical-neuron counts",
            method="RERconverge rate-phenotype correlation",
            inputs={"sets": requested_sets, "controls": control_names, "trait": trait_name, "min_species": min_species},
            details={
                "secondary": True,
                "primary_test": "erc",
                "trait": _trait_summary(trait_axis),
                "overclaim_guard": ASSOCIATION_ONLY_GUARD,
            },
            warnings=[
                ASSOCIATION_ONLY_GUARD,
                "RERconverge is secondary; ERC carries the coordinated-rate verdict.",
                "Phenotype association is underpowered and reported as N/A.",
            ],
            ci=_ci_unavailable("test skipped"),
        )

    rer = data.get("rerconverge") or {}
    if not rer or rer.get("status") != "computed":
        return _test_result(
            "rerconverge",
            available=False,
            skipped=True,
            skip_reason=(rer or {}).get("error") or "containerized RERconverge results unavailable",
            method="RERconverge rate-phenotype correlation",
            inputs={"sets": requested_sets, "controls": control_names, "trait": trait_name, "min_species": min_species},
            details={
                "secondary": True,
                "primary_test": "erc",
                "container_status": rer.get("status"),
                "trait": _trait_summary(trait_axis),
                "overclaim_guard": ASSOCIATION_ONLY_GUARD,
            },
            warnings=[ASSOCIATION_ONLY_GUARD, "RERconverge is secondary; ERC carries the coordinated-rate verdict."],
            ci=_ci_unavailable("test skipped"),
        )

    set_results = rer.get("set_results") or {}
    control_results = rer.get("control_results") or {}
    chosen = _best_rer_result(set_results, requested_sets)
    if not chosen:
        return _test_result(
            "rerconverge",
            available=False,
            skipped=True,
            skip_reason="requested RERconverge set results unavailable",
            method=rer.get("method") or "RERconverge rate-phenotype correlation",
            inputs={"sets": requested_sets, "controls": control_names, "trait": trait_name, "min_species": min_species},
            details={"available_sets": sorted(set_results), "secondary": True, "primary_test": "erc"},
            warnings=[ASSOCIATION_ONLY_GUARD, "RERconverge is secondary; ERC carries the coordinated-rate verdict."],
            ci=_ci_unavailable("test skipped"),
        )

    set_name, result = chosen
    control_values = [
        abs(float(r.get("r")))
        for name, r in control_results.items()
        if (not control_names or name in control_names) and r.get("r") is not None
    ]
    control_mean_abs = float(np.mean(control_values)) if control_values else None
    observed_r = _float_or_none(result.get("r"))
    p_value = _float_or_none(result.get("p_value"))
    effect = (
        abs(observed_r) - control_mean_abs
        if observed_r is not None and control_mean_abs is not None
        else None
    )
    primate_out_result = (rer.get("primate_out_results") or {}).get(set_name)
    primate_confounded, primate_note = _primate_confounded(observed_r, primate_out_result)
    warnings = [ASSOCIATION_ONLY_GUARD, "RERconverge is secondary; ERC carries the coordinated-rate verdict."]
    if primate_confounded is True:
        warnings.append("Association does not pass primate-out sensitivity; report as primate-confounded.")
    elif primate_confounded is None:
        warnings.append(primate_note or "Primate-out sensitivity result unavailable.")

    return _test_result(
        "rerconverge",
        n=result.get("n"),
        statistic=observed_r,
        p_value=p_value,
        significant=bool(p_value is not None and p_value < ALPHA),
        effect_size=effect,
        effect_size_name="abs_rer_trait_r_minus_control_mean_abs_r",
        ci=_ci_unavailable("container result does not provide a confidence interval"),
        method=rer.get("method") or "RERconverge rate-phenotype correlation",
        inputs={"sets": requested_sets, "controls": control_names, "trait": trait_name, "min_species": min_species},
        details={
            "secondary": True,
            "secondary_to": "erc",
            "primary_test": "erc",
            "trait": _trait_summary(trait_axis),
            "chosen_set": set_name,
            "set_results": set_results,
            "control_results": control_results,
            "control_mean_abs_r": control_mean_abs,
            "primate_out_result": primate_out_result,
            "primate_confounded": primate_confounded,
            "underpowered": bool(rer.get("underpowered") or trait_axis.get("underpowered")),
            "source": rer.get("source"),
            "tool_versions": rer.get("tool_versions") or {},
            "overclaim_guard": rer.get("overclaim_guard") or ASSOCIATION_ONLY_GUARD,
        },
        warnings=warnings,
        secondary=True,
        underpowered=bool(rer.get("underpowered") or trait_axis.get("underpowered")),
        primate_confounded=primate_confounded,
    )


def _trait_summary(axis: dict) -> dict:
    return {
        "name": axis.get("name"),
        "label": axis.get("label"),
        "usable_species": axis.get("usable_species"),
        "min_species": axis.get("min_species"),
        "underpowered": axis.get("underpowered"),
        "primate_coverage": axis.get("primate_coverage"),
        "non_primate_coverage": axis.get("non_primate_coverage"),
        "quality_counts": axis.get("quality_counts", {}),
    }


def _best_rer_result(results: dict, requested_sets: list[str]) -> tuple[str, dict] | None:
    candidates = []
    for name in requested_sets:
        result = results.get(name)
        r = _float_or_none((result or {}).get("r"))
        if result and r is not None:
            candidates.append((abs(r), name, result))
    if not candidates:
        return None
    _, name, result = sorted(candidates, reverse=True)[0]
    return name, result


def _primate_confounded(observed_r, primate_out_result: dict | None) -> tuple[bool | None, str | None]:
    if not primate_out_result:
        return None, "Primate-out sensitivity result unavailable."
    if "survives" in primate_out_result:
        return (not bool(primate_out_result.get("survives"))), None
    primate_r = _float_or_none(primate_out_result.get("r"))
    if observed_r is None or primate_r is None:
        return None, "Primate-out sensitivity result lacks a comparable correlation."
    same_sign = (observed_r == 0 and primate_r == 0) or (observed_r > 0) == (primate_r > 0)
    survives = same_sign and abs(primate_r) >= 0.5 * abs(observed_r)
    return (not survives), None


def _float_or_none(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


TEST_LIBRARY["mirrortree_lite"] = {
    "fn": mirrortree_lite,
    "kind": "rate_vectors",
    "constructs": {"cross_lineage_rate_correlation"},
}

TEST_LIBRARY["erc"] = {
    "fn": erc,
    "kind": "rate_vectors",
    "constructs": {"cross_lineage_rate_correlation"},
}

TEST_LIBRARY["rerconverge"] = {
    "fn": rerconverge_test,
    "kind": "rerconverge",
    "constructs": {"phenotype_association"},
}


def _is_numeric_list(v) -> bool:
    return isinstance(v, (list, tuple)) and all(isinstance(x, (int, float)) or x is None for x in v)


def _lookup(ref, data: dict):
    """Resolve a single reference to a list of numbers."""
    if _is_numeric_list(ref):
        return list(ref)
    if not isinstance(ref, str):
        return []
    variables = (data or {}).get("variables", {})
    if ref in variables:
        return list(variables[ref])
    if "." in ref:
        grp, _, metric = ref.partition(".")
        return list((data or {}).get("groups", {}).get(grp, {}).get(metric, []))
    # bare metric: try the first group that has it (rare; mostly callers pass group.metric)
    for g in (data or {}).get("groups", {}).values():
        if ref in g:
            return list(g[ref])
    return []


def _resolve_groups(inputs: dict, data: dict) -> dict:
    metric = inputs.get("metric")
    names = inputs.get("groups") or list((data or {}).get("groups", {}).keys())
    out = {}
    for n in names:
        if isinstance(n, str):
            if metric:
                out[n] = list((data or {}).get("groups", {}).get(n, {}).get(metric, []))
            else:
                # group given as a flat var/list
                out[n] = _lookup(n, data)
        elif _is_numeric_list(n):
            out[f"group{len(out)+1}"] = list(n)
    return out


def _resolve_table(inputs: dict, data: dict):
    t = inputs.get("table")
    if isinstance(t, str):
        return (data or {}).get("tables", {}).get(t, [])
    if isinstance(t, (list, tuple)):
        return [list(row) for row in t]
    return []


def _run_one(name: str, inputs: dict, data: dict) -> dict:
    spec = TEST_LIBRARY[name]
    fn, kind = spec["fn"], spec["kind"]
    inputs = inputs or {}
    try:
        if kind == "groups":
            if name == "mann_whitney_posthoc":
                correction = inputs.get("correction") or inputs.get("correction_method") or "fdr_bh"
                return fn(_resolve_groups(inputs, data), correction=correction)
            return fn(_resolve_groups(inputs, data))
        if kind == "xy":
            return fn(_lookup(inputs.get("x"), data), _lookup(inputs.get("y"), data))
        if kind == "ab":
            kw = {}
            if name == "permutation_test" and inputs.get("statistic"):
                kw["statistic"] = inputs["statistic"]
            return fn(_lookup(inputs.get("a"), data), _lookup(inputs.get("b"), data), **kw)
        if kind == "values":
            kw = {}
            if inputs.get("statistic"):
                kw["statistic"] = inputs["statistic"]
            return fn(_lookup(inputs.get("values") or inputs.get("x"), data), **kw)
        if kind == "table":
            return fn(_resolve_table(inputs, data))
        if kind == "paml":
            return fn(inputs, data)
        if kind == "rate_vectors":
            return fn((data or {}).get("rate_vectors") or {}, inputs)
        if kind == "rerconverge":
            return fn(inputs, data)
    except Exception as e:  # never let a bad plan crash the pipeline
        return _test_result(name or "unknown_test", error=f"{type(e).__name__}: {e}", inputs=inputs)
    return _test_result(name or "unknown_test", error="unhandled test kind", inputs=inputs)


def _closest_alternative(name: str) -> str:
    n = (name or "").lower()
    if "anova" in n or "t-test" in n or "ttest" in n:
        return "kruskal_wallis (non-parametric) or permutation_test"
    if "wilcox" in n or "rank-sum" in n or "rank sum" in n:
        return "mann_whitney_posthoc"
    if "correl" in n or "regress" in n:
        return "spearman or pearson"
    if "pgls" in n or "phylo" in n or "paml" in n or "codeml" in n:
        return "use paml_branch_model (branch model 2 LRT, requires codeml in PATH)"
    if "phenotype" in n or "rer" in n:
        return "rerconverge (secondary phenotype-association summary)"
    return f"none of the {len(TEST_LIBRARY)} library tests match; pick one of: {', '.join(TEST_LIBRARY)}"


def _data_summary(data: dict) -> dict:
    data = data or {}
    out = {
        "groups": {g: {m: len([x for x in vals if x is not None]) for m, vals in metrics.items()}
                   for g, metrics in data.get("groups", {}).items()},
        "variables": {v: len([x for x in vals if x is not None]) for v, vals in data.get("variables", {}).items()},
        "n_genes": len(data.get("gene_index", [])),
        "tables": list(data.get("tables", {}).keys()),
    }
    rate_vectors = data.get("rate_vectors") or {}
    if rate_vectors:
        out["rate_vectors"] = {
            "panel_species": len(rate_vectors.get("panel") or []),
            "sets": {k: len(v or []) for k, v in (rate_vectors.get("sets") or {}).items()},
            "genes_with_usable_rates": sum(
                1 for c in (rate_vectors.get("coverage") or {}).values()
                if c.get("usable_rates", 0) > 0
            ),
        }
        if rate_vectors.get("risk_filter"):
            out["rate_vectors"]["risk_filter"] = {
                "calibration_state": (rate_vectors.get("risk_filter") or {}).get("calibration_state"),
                "min_low_risk_genes": (rate_vectors.get("risk_filter") or {}).get("min_low_risk_genes"),
                "flagged_genes": (rate_vectors.get("risk_filter") or {}).get("flagged_genes", []),
                "excluded_genes": (rate_vectors.get("risk_filter") or {}).get("excluded_genes", []),
                "sets": (rate_vectors.get("risk_filter") or {}).get("sets", {}),
            }
    phenotypes = data.get("phenotypes") or {}
    if phenotypes:
        out["phenotypes"] = {
            name: {
                "usable_species": axis.get("usable_species"),
                "min_species": axis.get("min_species"),
                "underpowered": axis.get("underpowered"),
                "primate_coverage": axis.get("primate_coverage"),
                "non_primate_coverage": axis.get("non_primate_coverage"),
            }
            for name, axis in phenotypes.items()
        }
    rer = data.get("rerconverge") or {}
    if rer:
        out["rerconverge"] = {
            "status": rer.get("status"),
            "trait": rer.get("trait"),
            "secondary": True,
            "underpowered": rer.get("underpowered"),
            "primate_confounded": rer.get("primate_confounded"),
        }
    gnomad_prov = (data.get("provenance") or {}).get("gnomad")
    if gnomad_prov:
        out["gnomad_coverage"] = gnomad_prov
    compara_prov = (data.get("provenance") or {}).get("compara")
    if compara_prov:
        out["compara_coverage"] = compara_prov
    phylo_prov = (data.get("provenance") or {}).get("phylo")
    if phylo_prov:
        out["phylo_coverage"] = phylo_prov
    return out


def run_analysis_plan(plan: dict, data: dict) -> dict:
    """Execute a Methodologist plan against the prepared data dict.

    plan = {"tests_requested": [{"test": str, "inputs": dict, "rationale": str}, ...],
            "correction": "benjamini_hochberg"|"bonferroni"|"holm"|"none",
            "primary_tests": [...]}  (primary_tests echoed back, used by leave_one_out)
    """
    plan = plan or {}
    if plan.get("untestable"):
        reason = plan.get("untestable_reason") or "No compatible compute method for claim construct"
        result = _test_result(
            "untestable",
            requested=plan.get("required_construct") or "untestable",
            available=False,
            skipped=True,
            skip_reason=reason,
            method="construct-validity gate",
            details={"required_construct": plan.get("required_construct")},
        )
        return {
            "tests": [result],
            "corrections_applied": [],
            "data_summary": _data_summary(data),
            "correction_requested": plan.get("correction"),
            "untestable": True,
            "untestable_reason": reason,
            "required_construct": plan.get("required_construct"),
        }
    requested = plan.get("tests_requested") or []
    results: list[dict] = []
    for entry in requested:
        name = (entry or {}).get("test", "")
        rationale = (entry or {}).get("rationale")
        if name not in TEST_LIBRARY:
            results.append(_test_result(
                name or "unavailable_test",
                requested=name or "unavailable_test",
                available=False,
                error="test unavailable",
                details={"closest_alternative": _closest_alternative(name)},
                closest_alternative=_closest_alternative(name),
                rationale=rationale,
            ))
            continue
        res = _run_one(name, (entry or {}).get("inputs", {}), data)
        if "available" not in res:
            res["available"] = True
        if rationale:
            res["rationale"] = rationale
        results.append(validate_test_result(res))

    # multiple-testing correction across the family of p-values produced
    corr_key = _CORRECTION_ALIASES.get((plan.get("correction") or "").lower() if isinstance(plan.get("correction"), str)
                                       else plan.get("correction"), None)
    corrections_applied: list[dict] = []
    if corr_key:
        idx_p = [(i, r.get("p_value")) for i, r in enumerate(results)
                 if isinstance(r.get("p_value"), (int, float))]
        if len(idx_p) >= 2:
            c = _correction([p for _, p in idx_p], corr_key)
            for (i, _), p_adj, rej in zip(idx_p, c["pvals_adjusted"], c["reject"]):
                results[i]["p_value_adjusted"] = p_adj
                results[i]["significant_adjusted"] = bool(rej)
                validate_test_result(results[i])
            corrections_applied.append({"method": plan.get("correction"), "adjust_method": corr_key,
                                        "n_tests": len(idx_p), "alpha": ALPHA})
    results = [validate_test_result(r) for r in results]
    return {"tests": results, "corrections_applied": corrections_applied,
            "data_summary": _data_summary(data), "correction_requested": plan.get("correction")}


# ── reproducibility check (deterministic; moved from analyst.py) ─────────────
_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
_N_RE = re.compile(r"\bn\s*=\s*(\d+)", re.I)


def _parse_reported(stat_str: str, sample_size: str) -> dict:
    parsed: dict = {}
    s = f"{stat_str or ''} {sample_size or ''}"
    m = _N_RE.search(s)
    if m:
        parsed["n"] = int(m.group(1))
    elif sample_size:
        m2 = _NUM_RE.search(str(sample_size))
        if m2:
            parsed["n"] = int(float(m2.group(0)))
    low = (stat_str or "").lower()
    for key in ("rho", "spearman"):
        if key in low:
            nums = _NUM_RE.findall(stat_str or "")
            if nums:
                parsed["rho"] = float(nums[0])
            break
    if "r=" in low.replace(" ", "") or "pearson" in low:
        nums = _NUM_RE.findall(stat_str or "")
        if nums:
            parsed.setdefault("r", float(nums[0]))
    pm = re.search(r"p\s*[<=]\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", stat_str or "", re.I)
    if pm:
        parsed["p"] = float(pm.group(1))
    return parsed


def verify_reported_stats(completed_analysis: list[dict] | None, retrievable: dict | None) -> dict | None:
    """Deterministically cross-reference author-reported findings against the
    Ensembl-derived values available. Classifies each as
    ``agrees | disagrees | not_checkable_here`` (mostly the last — be honest)."""
    completed = completed_analysis or []
    if not completed:
        return None
    retrievable = retrievable or {}
    checks: list[dict] = []
    for f in completed:
        parsed = _parse_reported(f.get("statistic", ""), f.get("sample_size", ""))
        notes: list[str] = []
        classification = "not_checkable_here"
        n = parsed.get("n")
        if isinstance(n, int) and n < 6:
            notes.append(f"reported n={n} — far below what makes the cited test interpretable")
        finding_l = (f.get("finding", "") + " " + (f.get("statistic") or "")).lower()
        # only things derivable from Ensembl gene records are checkable here
        ensembl_derivable = any(k in finding_l for k in
                                ("dn/ds", "dnds", "ortholog", "paralog", "duplicat", "regulatory feature"))
        if ensembl_derivable and any(v.get("available") for v in retrievable.values()):
            notes.append("Ensembl-derived per-gene values are provided below for the Interpreter to reconcile")
        else:
            notes.append("not reconstructable from Ensembl gene records alone")
        checks.append({
            "reported": f.get("finding", ""),
            "reported_statistic": f.get("statistic"),
            "reported_test": f.get("test"),
            "reported_sample_size": f.get("sample_size"),
            "parsed": parsed or None,
            "classification": classification,
            "note": "; ".join(notes),
        })
    verifiable_count = sum(1 for v in retrievable.values() if v.get("available"))
    return {
        "reported_findings": completed,
        "ensembl_retrievable": retrievable,
        "not_verifiable_here": NOT_VERIFIABLE_HERE,
        "checks": checks,
        "verifiable_count": verifiable_count,
        "total": len(completed),
    }


# ── robustness: leave-one-out ───────────────────────────────────────────────
def _qualitative(test_result: dict) -> tuple:
    """(significant_bool_or_None, sign_of_statistic) — the 'story' of one test."""
    if not isinstance(test_result, dict) or test_result.get("error"):
        return (None, 0)
    sig = test_result.get("significant_adjusted")
    if sig is None:
        sig = test_result.get("significant")
    stat = test_result.get("statistic")
    return (bool(sig) if sig is not None else None,
            _sign(stat) if isinstance(stat, (int, float)) else 0)


def leave_one_out(starter_genes: list[str], primary_tests: list[dict],
                  rebuild_data: Callable[[set], dict],
                  thresholds: dict | None = None,
                  flagged_genes: list[str] | None = None) -> dict:
    """For each starter gene, drop it, re-run the primary tests, and check whether
    the qualitative outcome (significant?, direction) still matches the full-set run.

    primary_tests: list of {"test": name, "inputs": {...}} (input *references*, not arrays).
    rebuild_data(excluded: set[str]) -> data dict for the remaining genes.
    """
    thresholds = thresholds or {"stable": 0.8, "sensitive": 0.6}
    primary_tests = [t for t in (primary_tests or []) if (t or {}).get("test") in TEST_LIBRARY]
    full_data = rebuild_data(set())
    full = [_run_one(t["test"], t.get("inputs", {}), full_data) for t in primary_tests]
    full_story = [_qualitative(r) for r in full]

    if not primary_tests or not starter_genes:
        return {"applicable": bool(primary_tests),
                "reason": "no primary tests" if not primary_tests else "no starter genes",
                "status": "not_applicable" if not primary_tests else "skipped",
                "full_result": full, "perturbations": [], "agreement_fraction": 1.0,
                "stability": "stable", "most_influential_genes": []}

    if not any(sig is not None or sign != 0 for sig, sign in full_story):
        return {"applicable": False,
                "reason": "primary tests had insufficient results to perturb",
                "status": "skipped",
                "full_result": full, "perturbations": [], "agreement_fraction": 1.0,
                "stability": "unknown", "most_influential_genes": []}

    perturbations = []
    full_agree_count = 0
    influence: dict[str, int] = {}
    for g in starter_genes:
        data_g = rebuild_data({g})
        res_g = [_run_one(t["test"], t.get("inputs", {}), data_g) for t in primary_tests]
        story_g = [_qualitative(r) for r in res_g]
        matches = [story_g[i] == full_story[i] for i in range(len(primary_tests))]
        n_match = sum(1 for m in matches if m)
        all_match = all(matches)
        if all_match:
            full_agree_count += 1
        influence[g] = len(primary_tests) - n_match
        perturbations.append({
            "dropped_gene": g,
            "perturbation": "leave_one_out",
            "all_match": bool(all_match),
            "match_fraction": round(n_match / len(primary_tests), 4),
            "per_test": [{"test": primary_tests[i]["test"],
                          "full": {"significant": full_story[i][0], "sign": full_story[i][1]},
                          "without_gene": {"significant": story_g[i][0], "sign": story_g[i][1]},
                          "matched": bool(matches[i])} for i in range(len(primary_tests))],
        })
    agreement_fraction = round(full_agree_count / len(starter_genes), 4)
    stability = ("stable" if agreement_fraction >= thresholds["stable"]
                 else "sensitive" if agreement_fraction >= thresholds["sensitive"]
                 else "fragile")
    medium_risk_perturbation = None
    flagged = [g for g in dict.fromkeys(flagged_genes or []) if g in set(starter_genes)]
    if flagged:
        data_flagged = rebuild_data(set(flagged))
        res_flagged = [_run_one(t["test"], t.get("inputs", {}), data_flagged) for t in primary_tests]
        story_flagged = [_qualitative(r) for r in res_flagged]
        matches = [story_flagged[i] == full_story[i] for i in range(len(primary_tests))]
        n_match = sum(1 for m in matches if m)
        medium_risk_perturbation = {
            "perturbation": "drop_medium_risk_genes",
            "dropped_genes": flagged,
            "all_match": bool(all(matches)),
            "match_fraction": round(n_match / len(primary_tests), 4),
            "per_test": [{"test": primary_tests[i]["test"],
                          "full": {"significant": full_story[i][0], "sign": full_story[i][1]},
                          "without_medium_risk_genes": {"significant": story_flagged[i][0], "sign": story_flagged[i][1]},
                          "matched": bool(matches[i])} for i in range(len(primary_tests))],
        }
        perturbations.append(medium_risk_perturbation)
    most_influential = sorted([g for g, c in influence.items() if c > 0],
                              key=lambda g: -influence[g])
    return {
        "applicable": True,
        "status": "ran",
        "n_perturbations": len(starter_genes),
        "full_result": full,
        "perturbations": perturbations,
        "agreement_fraction": agreement_fraction,
        "stability": stability,
        "medium_risk_perturbation": medium_risk_perturbation,
        "medium_risk_stable": None if medium_risk_perturbation is None else medium_risk_perturbation["all_match"],
        "thresholds": thresholds,
        "most_influential_genes": most_influential,
    }


# ── smoke test ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    g = {"a": [1, 2, 3, 4, 5], "b": [3, 4, 5, 6, 7], "c": [10, 11, 12, 13, 14]}
    print("kruskal:", kruskal_wallis(g))
    print("mwu:", mann_whitney_posthoc({"a": g["a"], "c": g["c"]}))
    print("spearman:", spearman([1, 2, 3, 4, 5, 6], [2, 1, 4, 3, 6, 5]))
    print("pearson:", pearson([1, 2, 3, 4, 5], [2.1, 3.9, 6.2, 7.8, 10.1]))
    print("fisher:", fisher_exact([[8, 2], [1, 9]]))
    print("chi2:", chi_square([[10, 20, 30], [6, 9, 17]]))
    print("bh:", benjamini_hochberg([0.01, 0.02, 0.2, 0.5]))
    print("bonf:", bonferroni([0.01, 0.02, 0.2, 0.5]))
    print("boot:", bootstrap_ci([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], n_iter=1000))
    print("perm:", permutation_test([1, 2, 3, 4, 5], [4, 5, 6, 7, 8], n_iter=2000))
    print("cliffs:", cliffs_delta([1, 2, 3], [4, 5, 6]))
    print("cohens:", cohens_d([1, 2, 3, 4], [3, 4, 5, 6]))
    data = {"groups": {"syn": {"dnds": [0.1, 0.2, 0.15, 0.3]}, "ctrl": {"dnds": [0.4, 0.5, 0.45, 0.6]}},
            "variables": {"x": [1, 2, 3, 4, 5], "y": [2, 4, 5, 4, 5]}, "gene_index": ["A", "B"], "tables": {}}
    plan = {"tests_requested": [
        {"test": "kruskal_wallis", "inputs": {"metric": "dnds", "groups": ["syn", "ctrl"]}, "rationale": "compare dnds"},
        {"test": "spearman", "inputs": {"x": "x", "y": "y"}, "rationale": "monotone trend"},
        {"test": "made_up_test", "inputs": {}, "rationale": "n/a"},
    ], "correction": "benjamini_hochberg", "primary_tests": [
        {"test": "kruskal_wallis", "inputs": {"metric": "dnds", "groups": ["syn", "ctrl"]}}]}
    import json
    print("plan:", json.dumps(run_analysis_plan(plan, data), indent=2))
