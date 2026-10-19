"""C8: post-run root-cause classification of failed resources.

1. Read failed rows (status ``failed`` with an ``error_message``) from
   ``migration_progress``.  The state database is only read.
2. ``normalize_error_key()`` + :func:`rc_for_error` decide the RC code.
3. Optionally one model call per distinct ``(rc_code, error_key)`` adds an
   explanation and confidence; the model never changes the RC code.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aap_migration.ai.client import AIClient, is_enabled
from aap_migration.ai.prompts import MAX_SAMPLES, build_classification_prompt
from aap_migration.ai.rc_map import UNCLASSIFIED, rc_for_error
from aap_migration.ai.schemas import RC_CODES, FailureClassification
from aap_migration.cli.commands.migration_report_v2 import normalize_error_key
from aap_migration.migration.models import MigrationProgress
from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)

CSV_COLUMNS = [
    "resource_type",
    "source_id",
    "source_name",
    "rc_code",
    "error_key",
    "error_message",
    "ai_explanation",
    "ai_confidence",
]


@dataclass
class FailedResource:
    resource_type: str
    source_id: int
    source_name: str
    error_message: str
    error_key: str = ""
    rc_code: str = UNCLASSIFIED
    ai_explanation: str | None = None
    ai_confidence: str | None = None


@dataclass
class FailureGroup:
    rc_code: str
    error_key: str
    count: int
    resource_types: dict[str, int]
    sample_names: list[str]
    ai_explanation: str | None = None
    ai_confidence: str | None = None


@dataclass
class ClassificationResult:
    generated: str
    resources: list[FailedResource]
    groups: list[FailureGroup]
    per_rc: dict[str, int] = field(default_factory=dict)
    ai_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated": self.generated,
            "ai_used": self.ai_used,
            "total_failed": len(self.resources),
            "per_rc": self.per_rc,
            "groups": [asdict(g) for g in self.groups],
            "resources": [asdict(r) for r in self.resources],
        }


def _database_url(db: str | Path) -> str:
    """Accept a SQLAlchemy URL or a SQLite file path; open SQLite files read-only."""
    value = str(db)
    if "://" not in value:
        value = f"sqlite:///{value}"
    if value.startswith("sqlite:///") and "?" not in value:
        path = Path(value[len("sqlite:///") :]).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"State database not found: {path}")
        return f"sqlite:///file:{path}?mode=ro&uri=true"
    return value


def load_failed_rows(db: str | Path) -> list[FailedResource]:
    """Failed rows with an error message, ordered by type and source id."""
    engine = create_engine(_database_url(db))
    try:
        with Session(engine) as session:
            rows = session.execute(
                select(
                    MigrationProgress.resource_type,
                    MigrationProgress.source_id,
                    MigrationProgress.source_name,
                    MigrationProgress.error_message,
                )
                .where(MigrationProgress.status == "failed")
                .where(MigrationProgress.error_message.is_not(None))
                .where(MigrationProgress.error_message != "")
                .order_by(MigrationProgress.resource_type, MigrationProgress.source_id)
            ).all()
    finally:
        engine.dispose()
    return [
        FailedResource(
            resource_type=r.resource_type,
            source_id=r.source_id,
            source_name=r.source_name or "",
            error_message=r.error_message,
        )
        for r in rows
    ]


def classify_failures(
    resources: list[FailedResource],
    client: AIClient | None = None,
    use_ai: bool | None = None,
) -> ClassificationResult:
    """Classify failed resources by rules, then optionally explain each group."""
    for resource in resources:
        resource.error_key = normalize_error_key(resource.error_message)
        resource.rc_code = rc_for_error(resource.error_key, resource.resource_type)

    members: dict[tuple[str, str], list[FailedResource]] = defaultdict(list)
    for resource in resources:
        members[(resource.rc_code, resource.error_key)].append(resource)

    groups = [
        FailureGroup(
            rc_code=rc_code,
            error_key=error_key,
            count=len(items),
            resource_types=dict(sorted(Counter(r.resource_type for r in items).items())),
            sample_names=sorted({r.source_name for r in items})[:MAX_SAMPLES],
        )
        for (rc_code, error_key), items in sorted(members.items())
    ]

    available = client is not None or is_enabled()
    ai_used = available if use_ai is None else (use_ai and available)
    if ai_used:
        client = client or AIClient()
        for group in groups:
            _explain_group(client, group)
            for resource in members[(group.rc_code, group.error_key)]:
                resource.ai_explanation = group.ai_explanation
                resource.ai_confidence = group.ai_confidence

    counts = Counter(r.rc_code for r in resources)
    per_rc = {rc: counts.get(rc, 0) for rc in (*RC_CODES, UNCLASSIFIED)}
    return ClassificationResult(
        generated=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        resources=resources,
        groups=groups,
        per_rc=per_rc,
        ai_used=ai_used,
    )


def _explain_group(client: AIClient, group: FailureGroup) -> None:
    main_type = max(group.resource_types.items(), key=lambda kv: (kv[1], kv[0]))[0]
    system, user = build_classification_prompt(
        group.rc_code, group.error_key, main_type, group.sample_names, group.count
    )
    answer = client.complete_json(system, user, FailureClassification)
    if answer is None:
        return
    if answer.rc_code != group.rc_code:
        logger.warning("ai_rc_code_changed", expected=group.rc_code, got=answer.rc_code)
        return
    group.ai_explanation = answer.explanation
    group.ai_confidence = answer.confidence


def format_csv(result: ClassificationResult) -> str:
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for resource in result.resources:
        row = asdict(resource)
        writer.writerow({k: "" if row[k] is None else row[k] for k in CSV_COLUMNS})
    return out.getvalue()
