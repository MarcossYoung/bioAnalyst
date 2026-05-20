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
    return _test_result("cohens_d", n=[int(na), int(nb)], effect_size=d,
                        effect_size_name="cohens_d", statistic=d, p_value=None,
                        ci=None, significant=None, method="Cohen's d (pooled SD)")


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
    return _test_result("cliffs_delta", n=[int(len(a)), int(len(b))],
                        effect_size=delta, effect_size_name="cliffs_delta",
                        effect_size_label=label, statistic=delta, magnitude=label,
                        p_value=None, ci=None, significant=None, method="Cliff's delta")


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
        ci=None,
        method=f"Kruskal-Wallis H-test across {k} groups (scipy.stats.kruskal)",
    )


def mann_whitney_posthoc(groups: dict) -> dict:
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
                "significant": bool(p < ALPHA),
            })
    return _test_result(
        "mann_whitney_posthoc",
        n=sum(sum(pair.get("n", [])) for pair in pairs if isinstance(pair.get("n"), list)) or None,
        effect_size_name="cliffs_delta",
        method="Pairwise Mann-Whitney U with Cliff's delta (uncorrected p; apply a correction)",
        details={"pairs": pairs},
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


def spearman(x, y) -> dict:
    x, y = _paired(x, y)
    if len(x) < 3:
        return _test_result("spearman", error="need >=3 paired observations", n=int(len(x)))
    rho, p = sps.spearmanr(x, y)
    return _test_result("spearman", n=int(len(x)), statistic=rho,
                        effect_size=rho, effect_size_name="rho", p_value=p,
                        ci=_fisher_ci(rho, len(x)), significant=bool(p < ALPHA),
                        method="Spearman rank correlation (scipy.stats.spearmanr)")


def pearson(x, y) -> dict:
    x, y = _paired(x, y)
    if len(x) < 3:
        return _test_result("pearson", error="need >=3 paired observations", n=int(len(x)))
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
                        effect_size_name="odds_ratio", p_value=p, ci=None,
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
                        effect_size_name="cramers_v", ci=None, significant=bool(p < ALPHA),
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
                        effect_size=float(obs), effect_size_name=statistic, ci=None,
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
        return {"available": False, "requested": "paml_branch_model",
                "closest_alternative": "Compara pairwise dN/dS (already computed)"}
    best = min(computed, key=lambda x: x.get("lrt_pvalue", 1.0))
    from scipy.stats import chi2 as _chi2
    return {
        "test": "paml_branch_model", "available": True,
        "n": len(computed),
        "statistic": best["lrt_chi2"],
        "p_value": best["lrt_pvalue"],
        "significant": best["lrt_pvalue"] < 0.05,
        "effect_size": best.get("omega_foreground"),
        "effect_size_name": "omega_foreground",
        "effect_size_label": (
            "positive selection"
            if (best.get("omega_foreground") or 0) > 1 else "purifying/neutral"
        ),
        "ci_lower": None, "ci_upper": None,
        "per_gene": paml,
        "foreground_group": inputs.get("foreground", "primates"),
        "method": "PAML codeml branch model 2 LRT",
    }


# ── test library / plan dispatch ────────────────────────────────────────────
# name -> (callable, list-of-input-keys, group-or-pair-test?)
TEST_LIBRARY: dict[str, dict] = {
    "kruskal_wallis":      {"fn": kruskal_wallis,      "kind": "groups"},
    "mann_whitney_posthoc": {"fn": mann_whitney_posthoc, "kind": "groups"},
    "spearman":            {"fn": spearman,            "kind": "xy"},
    "pearson":             {"fn": pearson,             "kind": "xy"},
    "fisher_exact":        {"fn": fisher_exact,        "kind": "table"},
    "chi_square":          {"fn": chi_square,          "kind": "table"},
    "bootstrap_ci":        {"fn": bootstrap_ci,        "kind": "values"},
    "permutation_test":    {"fn": permutation_test,    "kind": "ab"},
    "cliffs_delta":        {"fn": cliffs_delta,        "kind": "ab"},
    "cohens_d":            {"fn": cohens_d,            "kind": "ab"},
    "paml_branch_model":   {"fn": _paml_branch_model,  "kind": "paml"},
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
Corrections: "benjamini_hochberg" (default for multi-test families), "bonferroni", "holm", "none"."""


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
                  thresholds: dict | None = None) -> dict:
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
                "full_result": full, "perturbations": [], "agreement_fraction": 1.0,
                "stability": "stable", "most_influential_genes": []}

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
    most_influential = sorted([g for g, c in influence.items() if c > 0],
                              key=lambda g: -influence[g])
    return {
        "applicable": True,
        "n_perturbations": len(starter_genes),
        "full_result": full,
        "perturbations": perturbations,
        "agreement_fraction": agreement_fraction,
        "stability": stability,
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
