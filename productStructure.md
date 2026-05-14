nullifier/
├── nullifier.py                  # CLI entrypoint (subcommands: run, review, flags, config)
├── config/
│   ├── __init__.py
│   ├── loader.py                 # Loads ~/.nullifier/config.toml, creates default if missing
│   └── default_config.toml       # Shipped default config
├── agents/
│   ├── __init__.py
│   ├── formalizer.py             # Two-stage extraction + confirmation gate
│   ├── librarian.py              # Per-paper classification (local) + per-claim synthesis (Claude)
│   ├── analyst.py                # NEW — genomic evidence via Ensembl
│   └── skeptic.py                # Stress test with literature + genomic evidence
├── tools/
│   ├── __init__.py
│   ├── llm_client.py             # Unified client routing to Claude OR LM Studio per agent
│   ├── literature.py             # Federated retrieval
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── semantic_scholar.py
│   │   ├── openalex.py
│   │   ├── europe_pmc.py
│   │   └── biorxiv.py
│   ├── ensembl.py                # NEW — 5 endpoints + SQLite cache
│   ├── query_expander.py
│   └── flag_store.py
├── report/
│   ├── __init__.py
│   └── renderer.py
├── review/
│   ├── __init__.py
│   └── interactive.py
├── examples/
│   └── synapse_bbb.txt
├── requirements.txt
└── README.md