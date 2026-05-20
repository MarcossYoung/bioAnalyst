"""One-time data preparation script.

Downloads main_HUMAN.csv from marcottelab/Gene-Ages, maps UniProt IDs to HGNC
gene symbols via UniProt search API, and writes liebeskind2016_gene_ages.tsv.

Run once from this directory:
    python prepare_liebeskind2016.py
"""
import csv
import io
import ssl
import time
import urllib.request
import urllib.parse

GENE_AGES_URL = (
    "https://raw.githubusercontent.com/marcottelab/Gene-Ages/master/Main/main_HUMAN.csv"
)
UNIPROT_SEARCH = "https://rest.uniprot.org/uniprotkb/search"
OUTPUT_FILE = "liebeskind2016_gene_ages.tsv"

# Bypass SSL verification for Windows environments missing CA certs
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

STRATUM_ORDER = [
    "Cellular_organisms",  # 1 — oldest
    "Euk_Archaea",         # 2
    "Euk+Bac",             # 3
    "Eukaryota",           # 4
    "Opisthokonta",        # 5
    "Eumetazoa",           # 6
    "Vertebrata",          # 7
    "Mammalia",            # 8
]
STRATUM_MAP = {name: i + 1 for i, name in enumerate(STRATUM_ORDER)}


def download_gene_ages() -> list[tuple[str, int, str]]:
    print("Downloading main_HUMAN.csv ...")
    with urllib.request.urlopen(GENE_AGES_URL, context=_CTX) as r:
        content = r.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    rows = []
    for row in reader:
        # The unnamed first column holds the UniProt accession
        uid = (row.get("") or row.get("﻿") or "").strip()
        mode_age = (row.get("modeAge") or "").strip()
        ps = STRATUM_MAP.get(mode_age)
        if uid and ps is not None:
            rows.append((uid, ps, mode_age))
    print(f"  {len(rows)} rows with valid modeAge")
    return rows


def fetch_gene_names_batch(ids: list[str]) -> dict[str, str]:
    """Return {uniprot_acc: gene_symbol} for a batch via UniProt search API."""
    query = " OR ".join(f"accession:{uid}" for uid in ids)
    params = urllib.parse.urlencode({
        "query": query,
        "fields": "accession,gene_names",
        "format": "tsv",
        "size": str(len(ids) + 10),
    })
    url = f"{UNIPROT_SEARCH}?{params}"
    with urllib.request.urlopen(url, context=_CTX) as r:
        text = r.read().decode("utf-8")
    result: dict[str, str] = {}
    for line in text.splitlines()[1:]:  # skip header
        parts = line.split("\t")
        if len(parts) >= 2:
            acc = parts[0].strip()
            gene_field = parts[1].strip()
            if acc and gene_field:
                # gene_names may be space-separated; take first token
                result[acc] = gene_field.split()[0].upper()
    return result


def build_symbol_map(uniprot_ids: list[str]) -> dict[str, str]:
    BATCH = 100  # keep URL length manageable
    symbol_map: dict[str, str] = {}
    total = len(uniprot_ids)
    for i in range(0, total, BATCH):
        batch = uniprot_ids[i : i + BATCH]
        if (i // BATCH) % 10 == 0:
            pct = 100 * i // total
            print(f"  {pct}% ({i}/{total}) ...")
        try:
            hits = fetch_gene_names_batch(batch)
            symbol_map.update(hits)
        except Exception as e:
            print(f"  WARNING: batch {i//BATCH + 1} failed — {e}")
        time.sleep(0.15)  # ~7 req/s, well within UniProt limits
    return symbol_map


def main():
    rows = download_gene_ages()
    uniprot_ids = [r[0] for r in rows]

    print(f"Mapping {len(uniprot_ids)} UniProt IDs to gene symbols ...")
    symbol_map = build_symbol_map(uniprot_ids)
    mapped = sum(1 for uid in uniprot_ids if uid in symbol_map)
    print(f"  Mapped {mapped} / {len(uniprot_ids)} IDs to gene symbols")

    seen: set[str] = set()
    written = skipped = 0
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["symbol", "phylostratum", "taxon_name"])
        for uid, ps, taxon in rows:
            sym = symbol_map.get(uid)
            if not sym:
                skipped += 1
                continue
            if sym in seen:
                continue
            seen.add(sym)
            writer.writerow([sym, ps, taxon])
            written += 1

    print(f"Wrote {written} rows to {OUTPUT_FILE} ({skipped} IDs without gene symbol)")


if __name__ == "__main__":
    main()
