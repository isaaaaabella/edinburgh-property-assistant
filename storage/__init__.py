"""Storage backend abstraction.

Picks `NotionStorage` or `LocalJSONStorage` based on
`STORAGE_BACKEND` env var (default: `local`).

`StorageBackend`, `NotionStorage`, and `LocalJSONStorage` are imported lazily
so this package stays importable while sub-modules are being built up.
"""

import os


def get_storage():
    """Construct the storage backend chosen by STORAGE_BACKEND env var."""
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
