"""File storage abstraction. Local filesystem for now, S3 later."""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import AppConfig


class LocalStorage:
    """Local filesystem storage for uploaded files."""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, filename: str, data: bytes) -> Path:
        """Save a file and return its path."""
        filepath = self.base_dir / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(data)
        return filepath

    def load(self, filename: str) -> bytes | None:
        """Load a file by name."""
        filepath = self.base_dir / filename
        if filepath.exists():
            return filepath.read_bytes()
        return None

    def delete(self, filename: str) -> bool:
        """Delete a file."""
        filepath = self.base_dir / filename
        if filepath.exists():
            filepath.unlink()
            return True
        return False

    def get_path(self, filename: str) -> Path:
        """Get the full path for a filename."""
        return self.base_dir / filename


def create_storage(config: AppConfig) -> LocalStorage:
    """Create a storage instance from config."""
    return LocalStorage(config.storage.file_dir)
