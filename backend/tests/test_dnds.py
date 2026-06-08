import pytest

from nullifier.tools.dnds import codon_align, ng86


def test_codon_align_threads_protein_gaps_and_terminal_stop():
    aligned = codon_align(
        "M-AF",
        "MTAF",
        "ATGGCTTTTTAA",
        "ATGACTGCTTTT",
    )

    assert aligned == ("ATG---GCTTTT", "ATGACTGCTTTT")


def test_codon_align_rejects_translation_mismatch():
    aligned = codon_align("MA", "MA", "ATGGCT", "ATGGAT")

    assert aligned is None


def test_ng86_identical_sequences_have_zero_ds_and_no_dnds():
    seq = "ATGGCTTTT" * 10

    result = ng86(seq, seq, min_codons=1)

    assert result["dn"] == pytest.approx(0.0)
    assert result["ds"] == pytest.approx(0.0)
    assert result["dnds"] is None


def test_ng86_counts_known_synonymous_and_nonsynonymous_case():
    # GCT->GCC is synonymous; ATG->ATA is nonsynonymous.
    a = ("GCT" * 5) + ("ATG" * 5) + ("TTT" * 30)
    b = ("GCC" * 5) + ("ATA" * 5) + ("TTT" * 30)

    result = ng86(a, b, min_codons=1)

    assert result["dn"] is not None
    assert result["ds"] is not None
    assert result["dnds"] is not None
    assert result["dnds"] > 0
    assert result["dnds"] < 1
