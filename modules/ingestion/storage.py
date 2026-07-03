from abc import ABC, abstractmethod
from pathlib import Path


class StorageBackend(ABC):
    """Abstraction over where raw uploaded document bytes are persisted.

    Local disk is the only implementation today; a future S3/GCS-backed
    implementation plugs in behind this same interface without touching
    callers (upload/process endpoints, tests).
    """

    @abstractmethod
    def save(self, key: str, data: bytes) -> str: ...

    @abstractmethod
    def read(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalFileStorage(StorageBackend):
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, data: bytes) -> str:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def read(self, key: str) -> bytes:
        return (self._root / key).read_bytes()

    def exists(self, key: str) -> bool:
        return (self._root / key).exists()
