import sys
from pathlib import Path
import tomllib  # Python 3.11+

CONFIG_DIR = Path.home() / ".nullifier"
CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_CONFIG = Path(__file__).parent / "default_config.toml"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base``; override wins on scalar leaves."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_config() -> dict:
    """Load config from ``~/.nullifier/config.toml``, merging packaged defaults over
    the top so new sections / keys are always available without the user re-creating
    their config. Creates the user file from the default on first run."""
    CONFIG_DIR.mkdir(exist_ok=True)
    defaults = _load_toml(DEFAULT_CONFIG)
    if not CONFIG_PATH.exists():
        import shutil
        shutil.copy(DEFAULT_CONFIG, CONFIG_PATH)
        print(f"Created default config at {CONFIG_PATH}", file=sys.stderr)
        cfg = defaults
    else:
        user = _load_toml(CONFIG_PATH)
        # Defaults first, user overlay on top — user values still win on scalar keys
        # but new sections / new routing keys always resolve.
        cfg = _deep_merge(defaults, user)

    # Expand ~ in paths
    cfg["ensembl"]["cache_path"] = str(Path(cfg["ensembl"]["cache_path"]).expanduser())
    cfg["flags"]["db_path"] = str(Path(cfg["flags"]["db_path"]).expanduser())

    # Stale model id warning — there is no "gemma-4". Surface once at load time.
    model = (cfg.get("backends", {}).get("local", {}).get("model") or "").lower()
    if "gemma-4" in model:
        print(
            f"[config] WARNING: backends.local.model is '{cfg['backends']['local']['model']}' — "
            f"there is no Gemma 4. Update {CONFIG_PATH} to the model id actually loaded in "
            "LM Studio (check /api/health when the server is running, e.g. 'google/gemma-3n-e4b').",
            file=sys.stderr,
        )
    return cfg


def show_config():
    print(f"Config file: {CONFIG_PATH}")
    if CONFIG_PATH.exists():
        print(CONFIG_PATH.read_text())
    else:
        print("(not yet created — will be initialized on first run)")
