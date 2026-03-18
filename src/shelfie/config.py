"""
config.py — Centralised runtime configuration for Shelfie.

All paths are derived from environment variables so the app works equally well
when installed via pip, run from source, or launched inside Docker.

Environment variables
---------------------
SHELFIE_DATA_DIR   Directory that holds the database and covers.
                     Default: ~/.shelfie
SHELFIE_DB_URL     Full SQLAlchemy database URL.
                     Default: sqlite:///<data_dir>/library.db
SHELFIE_COVERS_DIR Absolute path for generated cover images.
                     Default: <data_dir>/covers
SHELFIE_CONFIG     Path to the flat-file that stores the watched library folder.
                     Default: <data_dir>/library_path.txt
LIBRARY_PATH         Watched library folder (initial default if config file absent).
                     Default: ~/Books
"""

import os
from pathlib import Path

# ── Data root ──────────────────────────────────────────────────────────────────
# Everything the user cares about persisting lives under DATA_DIR.
DATA_DIR: Path = Path(
    os.environ.get("SHELFIE_DATA_DIR", str(Path.home() / ".shelfie"))
).expanduser().resolve()

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Database ───────────────────────────────────────────────────────────────────
DATABASE_URL: str = os.environ.get(
    "SHELFIE_DB_URL",
    f"sqlite:///{DATA_DIR / 'library.db'}",
)

# ── Cover images ───────────────────────────────────────────────────────────────
COVERS_DIR: Path = Path(
    os.environ.get("SHELFIE_COVERS_DIR", str(DATA_DIR / "covers"))
).expanduser().resolve()

COVERS_DIR.mkdir(parents=True, exist_ok=True)

# ── Library-path config file ───────────────────────────────────────────────────
# Stores the user's chosen watched folder as a single line of text.
CONFIG_FILE: Path = Path(
    os.environ.get("SHELFIE_CONFIG", str(DATA_DIR / "library_path.txt"))
).expanduser().resolve()

# ── Default library folder ─────────────────────────────────────────────────────
DEFAULT_LIBRARY_PATH: str = os.environ.get(
    "LIBRARY_PATH", str(Path.home() / "Books")
)

# ── Upload limits ──────────────────────────────────────────────────────────────
MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024          # 10 MB
ALLOWED_IMAGE_TYPES: frozenset = frozenset({"image/jpeg", "image/png", "image/webp"})
