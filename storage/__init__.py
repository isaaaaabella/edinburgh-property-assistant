"""Storage backend abstraction.

Picks `NotionStorage` or `LocalJSONStorage` based on
`STORAGE_BACKEND` env var (default: `local`).

`StorageBackend`, `NotionStorage`, and `LocalJSONStorage` are imported lazily
so this package stays importable while sub-modules are being built up.
"""

import os
from pathlib import Path


def _ensure_env_loaded():
    """Load property_assistant/.env into os.environ (setdefault) so that
    STORAGE_BACKEND and friends are honored even when callers haven't imported
    config.py. Without this, the backend silently falls back to 'local' unless
    the var is exported in the shell."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def get_storage():
    """Construct the storage backend chosen by STORAGE_BACKEND env var."""
    _ensure_env_loaded()
    backend = os.getenv("STORAGE_BACKEND", "local").lower().strip()
    if backend == "notion":
        from .notion_storage import NotionStorage
        return NotionStorage()
    if backend == "local":
        from .local_json_storage import LocalJSONStorage
        return LocalJSONStorage()
    raise ValueError(
        f"Unknown STORAGE_BACKEND={backend!r}. Expected 'notion' or 'local'."
    )


__all__ = ["get_storage"]
