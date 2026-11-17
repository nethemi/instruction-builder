from __future__ import annotations

import json
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import QualityReport


def append_metrics(
    path: Path,
    report: QualityReport,
    instruction_type: str,
    human_verdict: str = "pending",
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    record = {
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instruction_type": instruction_type,
        "score": report.score,
        "recommendation": report.recommendation,
        "critical_count": len(report.critical_issues),
        "unresolved_count": report.unresolved_count,
        "unsupported_ui_count": len(report.unsupported_ui),
        "unsupported_url_count": len(report.unsupported_urls),
        "change_ratio": report.change_ratio,
        "human_verdict": human_verdict,
        "duration_seconds": duration_seconds,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_metrics(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except (json.JSONDecodeError, TypeError):
            continue
    return records


def summarise(records: list[dict[str, Any]]) -> dict[str, float | int | None]:
    if not records:
        return {"count": 0, "median_score": None, "ready_rate": None, "median_change": None}
    scores = [float(item["score"]) for item in records if isinstance(item.get("score"), (int, float))]
    changes = [float(item["change_ratio"]) for item in records if isinstance(item.get("change_ratio"), (int, float))]
    ready = sum(item.get("recommendation") == "ready_for_human_review" for item in records)
    return {
        "count": len(records),
        "median_score": round(statistics.median(scores), 1) if scores else None,
        "ready_rate": round(ready / len(records) * 100, 1),
        "median_change": round(statistics.median(changes), 1) if changes else None,
    }
