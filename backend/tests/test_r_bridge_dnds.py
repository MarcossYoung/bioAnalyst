from nullifier.agents.analyst import _attach_rdnds_to_orthologs
from nullifier.tools import r_bridge


def test_pairwise_dnds_rejects_unusable_alignments():
    assert r_bridge.pairwise_dnds({}, use_cache=False) is None
    assert r_bridge.pairwise_dnds({"Pan_troglodytes": "ATGATG"}, use_cache=False) is None
    assert r_bridge.pairwise_dnds({"Homo_sapiens": "ATGA"}, use_cache=False) is None
    assert r_bridge.pairwise_dnds({"Homo_sapiens": "ATGATG"}, use_cache=False) is None


def test_pairwise_dnds_returns_none_when_rscript_missing(monkeypatch):
    monkeypatch.setattr(r_bridge, "_find_rscript", lambda _home: None)

    result = r_bridge.pairwise_dnds(
        {"Homo_sapiens": "ATG" * 4, "Pan_troglodytes": "ATG" * 4},
        use_cache=False,
    )

    assert result is None


def test_pairwise_dnds_parses_and_lowercases_rscript_output(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = "Pan_troglodytes\t0.250000\nMus_musculus\t0.500000\n"
        stderr = ""

    monkeypatch.setattr(r_bridge, "_find_rscript", lambda _home: "Rscript")
    monkeypatch.setattr(r_bridge.subprocess, "run", lambda *a, **k: FakeProc())

    result = r_bridge.pairwise_dnds(
        {
            "Homo_sapiens": "ATG" * 4,
            "Pan_troglodytes": "ATG" * 4,
            "Mus_musculus": "ATG" * 4,
        },
        use_cache=False,
    )

    assert result == {"pan_troglodytes": 0.25, "mus_musculus": 0.5}


def test_pairwise_dnds_logs_and_returns_none_on_rscript_failure(monkeypatch):
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "Error: there is no package called 'seqinr'\n"

    monkeypatch.setattr(r_bridge, "_find_rscript", lambda _home: "Rscript")
    monkeypatch.setattr(r_bridge.subprocess, "run", lambda *a, **k: FakeProc())

    result = r_bridge.pairwise_dnds(
        {"Homo_sapiens": "ATG" * 4, "Pan_troglodytes": "ATG" * 4},
        use_cache=False,
    )

    assert result is None


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
    assert gene_data["GENE1"]["orthologs"][0]["dnds_source"] == "homology_pal2nal_ng86"
    assert gene_data["GENE1"]["orthologs"][1]["dnds"] == 0.1
    assert gene_data["GENE1"]["orthologs"][1]["dnds_source"] == "ensembl_compara_dn_ds"
