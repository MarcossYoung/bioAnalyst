"""Tests for nullifier.tools.paml — 7 tests covering graceful degradation and LRT correctness."""
import json
import os
import tempfile
import subprocess
from unittest.mock import patch

import pytest

from nullifier.tools.paml import (
    _cache_get,
    _cache_set,
    _label_newick,
    _run_codeml,
    _write_phylip,
    _write_control,
    _parse_site_classes,
    _parse_beb_sites,
    run_branch_model,
    run_site_model,
    run_branch_site_model,
)
from nullifier.tools.compute import _paml_branch_model, _paml_site_model, _paml_branch_site_model

_PRIMATE_ALIGNED = {
    "sequences": {
        "Homo_sapiens":    "ATG" * 30,
        "Pan_troglodytes": "ATG" * 30,
        "Mus_musculus":    "ATG" * 30,
    },
    "newick": "(Homo_sapiens:0.01,Pan_troglodytes:0.005,Mus_musculus:0.1);",
}

_RODENT_ONLY = {
    "sequences": {"Mus_musculus": "ATG" * 30, "Rattus_norvegicus": "ATG" * 30},
    "newick": "(Mus_musculus:0.01,Rattus_norvegicus:0.02);",
}


def test_codeml_unavailable():
    with patch("nullifier.tools.paml._find_codeml", return_value=None):
        r = run_branch_model("ENSG00000001", "BRCA1", _PRIMATE_ALIGNED, use_cache=False)
    assert r["status"] == "codeml_unavailable"
    assert r["gene"] == "BRCA1"


def test_no_foreground_species():
    r = run_branch_model("ENSG00000002", "TP53", _RODENT_ONLY,
                         foreground="primates", use_cache=False)
    assert r["status"] == "no_foreground_seqs"


def test_lrt_computed():
    """Mock codeml to return fixed lnL values: lnL0=-1234.5, lnL2=-1230.0 → LRT≈9.0."""
    def fake_parse_lnl(path):
        return -1234.5 if "null" in path else -1230.0

    with patch("shutil.which", return_value="/usr/bin/codeml"), \
         patch("nullifier.tools.paml._run_codeml", return_value={"status": "ok"}), \
         patch("nullifier.tools.paml._parse_lnl", side_effect=fake_parse_lnl), \
         patch("nullifier.tools.paml._parse_omega_foreground", return_value=2.5):
        r = run_branch_model("ENSG00000003", "SHANK3", _PRIMATE_ALIGNED, use_cache=False)

    assert r["status"] == "computed"
    assert abs(r["lrt_chi2"] - 9.0) < 0.01
    assert r["lrt_pvalue"] < 0.05
    assert r["omega_foreground"] == 2.5


def test_codeml_nonzero_exit_returns_bounded_sanitized_diagnostics():
    output = b"x" * 2500 + b" C:\\temp\\paml\\failure"
    completed = subprocess.CompletedProcess([], 2, stdout=output, stderr=output)
    with patch("nullifier.tools.paml._find_codeml", return_value="codeml"), \
         patch("nullifier.tools.paml.subprocess.run", return_value=completed):
        result = _run_codeml("C:\\temp\\paml\\codeml.ctl", "C:\\temp\\paml")

    assert result["status"] == "error"
    assert result["returncode"] == 2
    assert len(result["stdout"]) <= 2000
    assert "C:\\temp\\paml" not in result["stdout"]
    assert "<workdir>" in result["stdout"]


def test_codeml_timeout_preserves_bounded_diagnostics():
    exc = subprocess.TimeoutExpired("codeml", 3, output=b"partial", stderr=b"stalled")
    with patch("nullifier.tools.paml._find_codeml", return_value="codeml"), \
         patch("nullifier.tools.paml.subprocess.run", side_effect=exc):
        result = _run_codeml("codeml.ctl", "C:\\temp\\paml", timeout=3)

    assert result == {
        "status": "timeout",
        "note": "codeml timed out after 3 seconds",
        "stdout": "partial",
        "stderr": "stalled",
    }


def test_branch_model_reports_codeml_phase_failure():
    diagnostic = {
        "status": "error",
        "returncode": 1,
        "stderr": "bad alignment",
        "stdout": "",
        "note": "codeml exited with a nonzero status",
    }
    with patch("nullifier.tools.paml._find_codeml", return_value="codeml"), \
         patch("nullifier.tools.paml._run_codeml", return_value=diagnostic):
        result = run_branch_model(
            "ENSG00000003", "SHANK3", _PRIMATE_ALIGNED, use_cache=False
        )

    assert result["status"] == "error"
    assert result["phase"] == "null"
    assert result["returncode"] == 1
    assert result["stderr"] == "bad alignment"


def test_cache_roundtrip():
    import nullifier.tools.paml as paml_mod
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        orig_path = paml_mod._CACHE_PATH
        orig_conn = paml_mod._conn
        paml_mod._CACHE_PATH = Path(tmp) / "test_paml.db"
        paml_mod._conn = None
        try:
            key = "ENSG_test:primates:abc123"
            payload = {"status": "computed", "gene": "TEST", "lrt_pvalue": 0.01}
            _cache_set(key, payload)
            hit = _cache_get(key)
            assert hit == payload
        finally:
            if paml_mod._conn is not None:
                paml_mod._conn.close()
                paml_mod._conn = None
            paml_mod._CACHE_PATH = orig_path
            paml_mod._conn = orig_conn


def test_control_file_supports_site_and_branch_site_parameters():
    with tempfile.TemporaryDirectory() as tmp:
        ctl = _write_control(tmp, 2, "a.phy", "t.nwk", "out.mlc",
                             nssites=2, ncatg=10, fix_omega=1, omega=1.0,
                             run_label="branch_site_null")
        text = open(ctl).read()
    assert "model    = 2" in text
    assert "NSsites  = 2" in text
    assert "ncatG    = 10" in text
    assert "fix_omega = 1" in text
    assert "omega    = 1.0" in text


def test_site_class_and_beb_parsers():
    with tempfile.TemporaryDirectory() as tmp:
        mlc = os.path.join(tmp, "m8.mlc")
        with open(mlc, "w") as handle:
            handle.write("""p: 0.2 0.3 0.5\nw: 0.1 0.8 3.4\nBayes Empirical Bayes (BEB) analysis\n 42 K 0.987**\n 77 0.951 *\n\n""")
        assert _parse_site_classes(mlc) == {"omega": 3.4, "proportion": 0.5}
        sites = _parse_beb_sites(mlc)
    assert sites[0] == {"position": 42, "amino_acid": "K", "posterior": 0.987, "significance_marker": "**"}
    assert sites[1]["amino_acid"] is None


def test_site_and_branch_site_lrt_definitions():
    def fake_pair(*args, **kwargs):
        prep = {"seqs": _PRIMATE_ALIGNED["sequences"], "foreground": {"Homo_sapiens"}}
        parsed = {"lnl_null": -105.0, "lnl_alt": -100.0, "classes": {"omega": 4.0, "proportion": 0.1},
                  "beb_sites": [{"position": 2, "posterior": 0.99}], "omega_fg": 4.0,
                  "cache_key": "test"}
        return prep, parsed, None
    with patch("nullifier.tools.paml._run_pair", side_effect=fake_pair):
        site = run_site_model("ENSG", "GENE", _PRIMATE_ALIGNED, use_cache=False)
        branch_site = run_branch_site_model("ENSG", "GENE", _PRIMATE_ALIGNED, use_cache=False)
    assert site["lrt_statistic"] == 10.0
    assert site["lrt_pvalue"] == pytest.approx(0.006738, abs=1e-6)
    assert branch_site["lrt_pvalue"] == pytest.approx(0.000783, abs=1e-6)


def test_paml_consumers_bh_correct_and_require_beb_support():
    records = {
        "A": {"status": "computed", "gene": "A", "lrt_pvalue": 0.01, "lrt_statistic": 8,
              "omega_positive_class": 3.0, "prop_positive": 0.1, "beb_sites": []},
        "B": {"status": "computed", "gene": "B", "lrt_pvalue": 0.02, "lrt_statistic": 7,
              "omega_positive_class": 2.0, "prop_positive": 0.2,
              "beb_sites": [{"position": 4, "posterior": 0.98}]},
        "C": {"status": "computed", "gene": "C", "lrt_pvalue": 0.5, "lrt_statistic": 1,
              "omega_positive_class": 1.2, "prop_positive": 0.3, "beb_sites": []},
    }
    result = _paml_site_model({}, {"paml_site": records})
    assert result["p_value_adjusted"] == 0.03
    assert result["significant"] is False
    assert result["per_gene"]["B"]["p_value_adjusted"] == 0.03
    assert result["internally_corrected"] is True

    bs = {k: {**v, "omega_foreground_positive": v["omega_positive_class"], "prop_sites": v["prop_positive"]}
          for k, v in records.items()}
    branch_site = _paml_branch_site_model({}, {"paml_branch_site": bs})
    assert branch_site["method"].endswith("mixture-null LRT")


def test_aggregate_no_data():
    result = _paml_branch_model({}, {"paml": {}})
    assert result["available"] is False
    assert "Compara pairwise" in result["closest_alternative"]


def test_write_phylip_format():
    seqs = {"Homo_sapiens": "ATGATG", "Mus_musculus": "ATGATG"}
    with tempfile.NamedTemporaryFile(mode="r", suffix=".phy", delete=False) as f:
        fname = f.name
    try:
        _write_phylip(seqs, fname)
        with open(fname) as f:
            lines = f.readlines()
        header = lines[0].split()
        assert header[0] == "2"   # n_taxa
        assert header[1] == "6"   # seq_len
        assert len(lines) == 3    # header + 2 sequences
    finally:
        os.unlink(fname)


def test_label_newick():
    newick = "(Homo_sapiens:0.01,Mus_musculus:0.1)"
    labelled = _label_newick(newick, {"Homo_sapiens"})
    assert "Homo_sapiens #1:0.01" in labelled
    assert "Mus_musculus:0.1" in labelled
    assert "Mus_musculus #1" not in labelled
