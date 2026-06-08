"""Pairwise codon dN/dS helpers.

Implements a small pal2nal threader plus Nei-Gojobori 1986 counting with
Jukes-Cantor correction. The estimator is intentionally conservative: any
translation mismatch, stop codon in the aligned body, zero/saturated dS, or too
little aligned codon sequence yields ``dnds=None`` rather than a fabricated
omega.
"""
from __future__ import annotations

import itertools
import math
import re

import numpy as np


CODON_TABLE = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W",
    "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R",
    "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}
STOP_CODONS = {c for c, aa in CODON_TABLE.items() if aa == "*"}
NUCLEOTIDES = ("A", "C", "G", "T")
_DNA_RE = re.compile(r"[^ACGT]", re.I)


def _clean_cds(seq: str | None) -> str:
    return _DNA_RE.sub("", str(seq or "").upper().replace("U", "T"))


def translate(seq: str | None, *, strip_terminal_stop: bool = True) -> str | None:
    cds = _clean_cds(seq)
    if len(cds) < 3 or len(cds) % 3:
        return None
    if strip_terminal_stop and cds[-3:] in STOP_CODONS:
        cds = cds[:-3]
    aas = []
    for i in range(0, len(cds), 3):
        aa = CODON_TABLE.get(cds[i:i + 3])
        if aa is None:
            return None
        aas.append(aa)
    return "".join(aas)


def _clean_protein_alignment(seq: str | None) -> str:
    return str(seq or "").upper().replace(" ", "").replace(".", "-")


def _ungapped_protein(seq: str | None) -> str:
    return _clean_protein_alignment(seq).replace("-", "").rstrip("*")


def _strip_terminal_stop(cds: str) -> str:
    return cds[:-3] if len(cds) >= 3 and cds[-3:] in STOP_CODONS else cds


def codon_align(
    prot_aln_a: str | None,
    prot_aln_b: str | None,
    cds_a: str | None,
    cds_b: str | None,
) -> tuple[str, str] | None:
    """Thread two CDS sequences through an already-paired protein alignment."""
    pa = _clean_protein_alignment(prot_aln_a)
    pb = _clean_protein_alignment(prot_aln_b)
    if not pa or not pb or len(pa) != len(pb):
        return None

    ca = _strip_terminal_stop(_clean_cds(cds_a))
    cb = _strip_terminal_stop(_clean_cds(cds_b))
    ta = translate(ca, strip_terminal_stop=False)
    tb = translate(cb, strip_terminal_stop=False)
    if ta is None or tb is None:
        return None
    if ta != _ungapped_protein(pa) or tb != _ungapped_protein(pb):
        return None

    out_a: list[str] = []
    out_b: list[str] = []
    ia = ib = 0
    for aa, bb in zip(pa, pb):
        if aa == "-":
            out_a.append("---")
        else:
            codon = ca[ia:ia + 3]
            if len(codon) != 3:
                return None
            out_a.append(codon)
            ia += 3
        if bb == "-":
            out_b.append("---")
        else:
            codon = cb[ib:ib + 3]
            if len(codon) != 3:
                return None
            out_b.append(codon)
            ib += 3
    if ia != len(ca) or ib != len(cb):
        return None
    return "".join(out_a), "".join(out_b)


def _valid_sense_codon(codon: str) -> bool:
    return len(codon) == 3 and codon in CODON_TABLE and codon not in STOP_CODONS


def _site_counts(codon: str) -> tuple[float, float]:
    syn = 0
    aa = CODON_TABLE[codon]
    chars = list(codon)
    for pos, original in enumerate(chars):
        for nt in NUCLEOTIDES:
            if nt == original:
                continue
            mutated = chars.copy()
            mutated[pos] = nt
            m = "".join(mutated)
            if not _valid_sense_codon(m):
                continue
            if CODON_TABLE[m] == aa:
                syn += 1
    s_sites = syn / 3.0
    return 3.0 - s_sites, s_sites


def _path_substitution_counts(codon_a: str, codon_b: str) -> tuple[float, float] | None:
    diffs = [i for i, (a, b) in enumerate(zip(codon_a, codon_b)) if a != b]
    if not diffs:
        return 0.0, 0.0
    totals = []
    for order in itertools.permutations(diffs):
        current = list(codon_a)
        syn = nonsyn = 0
        ok = True
        for pos in order:
            before = "".join(current)
            current[pos] = codon_b[pos]
            after = "".join(current)
            if not _valid_sense_codon(before) or not _valid_sense_codon(after):
                ok = False
                break
            if CODON_TABLE[before] == CODON_TABLE[after]:
                syn += 1
            else:
                nonsyn += 1
        if ok:
            totals.append((nonsyn, syn))
    if not totals:
        return None
    arr = np.asarray(totals, dtype=float)
    return float(arr[:, 0].mean()), float(arr[:, 1].mean())


def _jc_distance(p: float) -> float | None:
    if p < 0 or p >= 0.75:
        return None
    return float(-0.75 * math.log(1.0 - (4.0 * p / 3.0)))


def ng86(codon_a: str, codon_b: str, *, min_codons: int = 30) -> dict:
    """Estimate pairwise dN/dS with Nei-Gojobori counting and JC correction."""
    if not codon_a or not codon_b or len(codon_a) != len(codon_b):
        return {"dnds": None, "dn": None, "ds": None, "n_sites": 0.0, "s_sites": 0.0}
    if len(codon_a) % 3:
        return {"dnds": None, "dn": None, "ds": None, "n_sites": 0.0, "s_sites": 0.0}

    n_sites = s_sites = 0.0
    n_diffs = s_diffs = 0.0
    compared = 0
    for i in range(0, len(codon_a), 3):
        ca = codon_a[i:i + 3].upper()
        cb = codon_b[i:i + 3].upper()
        if "---" in (ca, cb):
            continue
        if "-" in ca or "-" in cb:
            continue
        if not _valid_sense_codon(ca) or not _valid_sense_codon(cb):
            continue
        na, sa = _site_counts(ca)
        nb, sb = _site_counts(cb)
        n_sites += (na + nb) / 2.0
        s_sites += (sa + sb) / 2.0
        counts = _path_substitution_counts(ca, cb)
        if counts is not None:
            nd, sd = counts
            n_diffs += nd
            s_diffs += sd
        compared += 1

    if compared < min_codons or n_sites <= 0 or s_sites <= 0:
        return {"dnds": None, "dn": None, "ds": None, "n_sites": n_sites, "s_sites": s_sites}

    p_n = n_diffs / n_sites
    p_s = s_diffs / s_sites
    dn = _jc_distance(p_n)
    ds = _jc_distance(p_s)
    dnds = (dn / ds) if dn is not None and ds is not None and ds > 0 else None
    return {
        "dnds": dnds,
        "dn": dn,
        "ds": ds,
        "n_sites": n_sites,
        "s_sites": s_sites,
        "compared_codons": compared,
        "pN": p_n,
        "pS": p_s,
    }
