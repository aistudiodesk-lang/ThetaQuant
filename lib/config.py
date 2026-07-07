"""
lib/config.py — single source of truth for filesystem paths, env-overridable.

WHY THIS EXISTS (portability keystone)
--------------------------------------
The app must run three ways from ONE codebase, with no code changes:
  1. Native on the Mac mini  (defaults below — exactly today's behaviour)
  2. In a Docker container    (paths point at mounted volumes via env vars)
  3. On AWS / any host later  (same container; env vars point at EBS / EFS / etc.)

Every path here has a default that reproduces the current native layout, so
setting NOTHING changes nothing. A container/host overrides only what it needs.

HARD RULE (unchanged): secrets NEVER live in code or git. They live as runtime
files under CONFIG_DIR (default ~/.config), mounted read-only into a container.
Backtest datasets (Parquet) stay LOCAL — see DATA_DIR / PARQUET_DIR; never upload.

Env vars (all optional):
  TQ_ROOT         repo root                        (default: this repo)
  TQ_DATA_DIR     writable app data                (default: <root>/data)
  TQ_CONFIG_DIR   runtime secrets/creds            (default: ~/.config)
  TQ_PARQUET_DIR  backtest parquet store           (default: <data>/parquet)
  TQ_JOURNAL      trade journal jsonl              (default: <data>/trade_journal.jsonl)
  TQ_SNAPSHOTS    dashboard snapshots dir          (default: <data>/dashboard_snapshots)
"""
from __future__ import annotations
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_path(var: str, default: Path) -> Path:
    """Path from env var (with ~ expansion) or the given default."""
    v = os.environ.get(var)
    return Path(v).expanduser().resolve() if v else default


ROOT: Path = _env_path("TQ_ROOT", _REPO_ROOT)
DATA_DIR: Path = _env_path("TQ_DATA_DIR", ROOT / "data")
CONFIG_DIR: Path = _env_path("TQ_CONFIG_DIR", Path.home() / ".config")
PARQUET_DIR: Path = _env_path("TQ_PARQUET_DIR", DATA_DIR / "parquet")
JOURNAL: Path = _env_path("TQ_JOURNAL", DATA_DIR / "trade_journal.jsonl")
SNAPSHOTS_DIR: Path = _env_path("TQ_SNAPSHOTS", DATA_DIR / "dashboard_snapshots")


def config_file(name: str) -> Path:
    """A runtime secret/cred file under CONFIG_DIR (e.g. kite_credentials.json)."""
    return CONFIG_DIR / name


def ensure_dirs() -> None:
    """Create writable dirs if missing (safe to call on startup)."""
    for d in (DATA_DIR, SNAPSHOTS_DIR, JOURNAL.parent):
        d.mkdir(parents=True, exist_ok=True)
