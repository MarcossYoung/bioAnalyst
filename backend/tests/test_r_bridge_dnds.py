import builtins
import sys
import types

from nullifier.agents.analyst import _attach_rdnds_to_orthologs
from nullifier.tools import r_bridge


def test_pairwise_dnds_rejects_unusable_alignments():
    assert r_bridge.pairwise_dnds({}, use_cache=False) is None
    assert r_bridge.pairwise_dnds({"Pan_troglodytes": "ATGATG"}, use_cache=False) is None
    assert r_bridge.pairwise_dnds({"Homo_sapiens": "ATGA"}, use_cache=False) is None
    assert r_bridge.pairwise_dnds({"Homo_sapiens": "ATGATG"}, use_cache=False) is None


def test_pairwise_dnds_gracefully_handles_missing_rpy2(monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("rpy2"):
            raise ImportError("no rpy2")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(r_bridge, "_configure_r_home", lambda: True)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = r_bridge.pairwise_dnds(
        {"Homo_sapiens": "ATG" * 4, "Pan_troglodytes": "ATG" * 4},
        use_cache=False,
    )

    assert result is None


def test_pairwise_dnds_lowercases_fake_r_output(monkeypatch):
    class FakeVector(list):
        names = ["Pan_troglodytes"]

    def fake_r(_code):
        def _fn(_names, _seqs, _ref):
            return FakeVector([0.25])
        return _fn

    fake_rpy2 = types.ModuleType("rpy2")
    fake_robjects = types.ModuleType("rpy2.robjects")
    fake_packages = types.ModuleType("rpy2.robjects.packages")
    fake_robjects.r = fake_r
    fake_robjects.StrVector = lambda values: list(values)
    fake_packages.importr = lambda _name: object()
    fake_rpy2.robjects = fake_robjects

    monkeypatch.setattr(r_bridge, "_configure_r_home", lambda: True)
    monkeypatch.setitem(sys.modules, "rpy2", fake_rpy2)
    monkeypatch.setitem(sys.modules, "rpy2.robjects", fake_robjects)
    monkeypatch.setitem(sys.modules, "rpy2.robjects.packages", fake_packages)

    result = r_bridge.pairwise_dnds(
        {"Homo_sapiens": "ATG" * 4, "Pan_troglodytes": "ATG" * 4},
        use_cache=False,
    )

    assert result == {"pan_troglodytes": 0.25}


def test_attach_rdnds_to_orthologs_lowercase_species_join():
    gene_data = {
        "GENE1": {
            "orthologs": [
                {"target_species": "pan_troglodytes", "dnds": None},
                {"target_species": "Mus_musculus", "dnds": 0.1},
            ],
        },
    }

    attached = _attach_rdnds_to_orthologs(
        gene_data,
        {"GENE1": {"pan_troglodytes": 0.22, "mus_musculus": 0.33}},
    )

    assert attached == 1
    assert gene_data["GENE1"]["orthologs"][0]["dnds"] == 0.22
    assert gene_data["GENE1"]["orthologs"][0]["dnds_source"] == "r_seqinr_kaks"
    assert gene_data["GENE1"]["orthologs"][1]["dnds"] == 0.1
    assert gene_data["GENE1"]["orthologs"][1]["dnds_source"] == "ensembl_compara"
