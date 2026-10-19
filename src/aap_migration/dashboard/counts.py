"""Per-resource-type counts for the dashboard.

* source:   objects in the export directory (``<export>/<type>/*.json``)
* target:   ``count`` from a read-only ``GET <target>/<type>/?page_size=1``
* migrated: ``completed`` rows in ``migration_progress`` (read-only)

All counts are cached together for ``ttl_seconds`` (5 s by default).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from aap_migration.ai.classify import read_only_database_url
from aap_migration.migration.models import MigrationProgress
from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)

CACHE_TTL_SECONDS = 5.0


def source_counts(export_dir: Path | None) -> dict[str, int]:
    """Objects per resource type in the export directory; {} when there is none yet."""
    if export_dir is None or not export_dir.is_dir():
        return {}
    counts: dict[str, int] = {}
    for resource_dir in sorted(p for p in export_dir.iterdir() if p.is_dir()):
        total = 0
        for json_file in resource_dir.glob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, list):
                total += sum(1 for item in data if isinstance(item, dict))
        counts[resource_dir.name] = total
    return counts


def state_counts(db: str | None) -> dict[str, dict[str, int]]:
    """``{resource_type: {status: n}}`` from the state database; {} when unavailable."""
    if not db:
        return {}
    try:
        engine = create_engine(read_only_database_url(db))
    except FileNotFoundError:
        return {}
    try:
        with Session(engine) as session:
            rows = session.execute(
                select(MigrationProgress.resource_type, MigrationProgress.status, func.count())
                .group_by(MigrationProgress.resource_type, MigrationProgress.status)
            ).all()
    except Exception as e:  # a busy or half-written DB must not stop the page
        logger.warning("dashboard_state_db_unreadable", error_type=type(e).__name__)
        return {}
    finally:
        engine.dispose()
    counts: dict[str, dict[str, int]] = {}
    for resource_type, status, n in rows:
        counts.setdefault(resource_type, {})[status] = n
    return counts


class TargetCounter:
    """Read-only object counts from the AAP 2.6 controller API."""

    def __init__(
        self,
        url: str,
        token: str,
        verify_ssl: bool = True,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._verify = verify_ssl
        self._timeout = timeout
        self._transport = transport

    def counts(self, resource_types: list[str]) -> dict[str, int | None]:
        result: dict[str, int | None] = {}
        with httpx.Client(
            verify=self._verify, timeout=self._timeout, transport=self._transport
        ) as client:
            for resource_type in resource_types:
                result[resource_type] = self._count(client, resource_type)
        return result

    def _count(self, client: httpx.Client, resource_type: str) -> int | None:
        try:
            response = client.get(
                f"{self.url}/{resource_type}/", params={"page_size": 1}, headers=self._headers
            )
            if response.status_code == 404:
                return None  # not an API endpoint (e.g. an export-only type)
            response.raise_for_status()
            count = response.json().get("count")
            return count if isinstance(count, int) else None
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("dashboard_target_count_failed", resource_type=resource_type, error_type=type(e).__name__)
            return None


class CountsProvider:
    """Combines the three sources and caches the result for ``ttl_seconds``."""

    def __init__(
        self,
        export_dir: Path | None,
        db: str | None,
        target: TargetCounter | None,
        ttl_seconds: float = CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.export_dir = export_dir
        self.db = db
        self.target = target
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0

    def get(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            if self._cached is None or now - self._cached_at >= self.ttl_seconds:
                self._cached = self._compute()
                self._cached_at = now
            return self._cached

    def _compute(self) -> dict[str, Any]:
        source = source_counts(self.export_dir)
        state = state_counts(self.db)
        types = sorted(set(source) | set(state))
        target = self.target.counts(types) if self.target else {}
        rows = [
            {
                "resource_type": t,
                "source": source.get(t),
                "target": target.get(t),
                "migrated": state.get(t, {}).get("completed", 0),
                "failed": state.get(t, {}).get("failed", 0),
            }
            for t in types
        ]
        return {
            "generated": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "target_connected": self.target is not None,
            "resource_types": rows,
            "totals": {
                key: sum(r[key] or 0 for r in rows) for key in ("source", "target", "migrated", "failed")
            },
        }
