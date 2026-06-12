from nullifier.tools.phenotypes import (
    ASSOCIATION_ONLY_GUARD,
    build_cortical_neuron_axis,
    load_cortical_neurons,
)


def test_load_cortical_neurons_computes_log_axis():
    records = load_cortical_neurons()

    assert records["homo_sapiens"]["cortical_neurons_millions"] == 16000
    assert records["homo_sapiens"]["log10_cortical_neurons"] is not None


def test_build_cortical_neuron_axis_tracks_overlap_and_primate_coverage():
    panel = [
        "homo_sapiens",
        "pan_troglodytes",
        "mus_musculus",
        "rattus_norvegicus",
        "missing_species",
    ]

    axis = build_cortical_neuron_axis(panel, min_species=4)

    assert axis["name"] == "cortical_neurons"
    assert axis["available"] is True
    assert axis["usable_species"] == 4
    assert axis["primate_coverage"] == 2
    assert axis["non_primate_coverage"] == 2
    assert "missing_species" in axis["missing_species"]
    assert axis["overclaim_guard"] == ASSOCIATION_ONLY_GUARD


def test_build_cortical_neuron_axis_marks_underpowered_below_floor():
    axis = build_cortical_neuron_axis(["homo_sapiens", "mus_musculus"], min_species=20)

    assert axis["available"] is False
    assert axis["underpowered"] is True
    assert "need >= 20" in axis["reason"]
