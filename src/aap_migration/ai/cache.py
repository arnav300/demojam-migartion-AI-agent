"""JSON file cache of validated model answers, keyed by a hash of the request.

One rehearsal run with ``AI_CACHE_FILE`` set fills the cache; later runs replay
the same answers instantly without calling the model.  Only answers that passed
schema validation (and the caller's checks) are stored.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


def prompt_hash(system: str, user: str, schema: str = "", model: str = "") -> str:
    payload = "\n\x00\n".join((model, schema, system, user))
    return hashlib.sha256(payload.encode()).hexdigest()


class PromptCache:
    """Thread-safe ``hash -> validated answer`` store, saved after every write."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if self.path.is_file():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = loaded
            except (OSError, json.JSONDecodeError):
                logger.warning("ai_cache_unreadable", path=str(self.path))

    def get(self, key: str, schema: type[T]) -> T | None:
        with self._lock:
            entry = self._data.get(key)
        if entry is None:
            return None
        try:
            return schema.model_validate(entry)
        except ValidationError:
            return None

    def put(self, key: str, value: BaseModel) -> None:
        with self._lock:
            self._data[key] = value.model_dump()
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    json.dumps(self._data, indent=1, sort_keys=True), encoding="utf-8"
                )
            except OSError:
                logger.warning("ai_cache_not_writable", path=str(self.path))
