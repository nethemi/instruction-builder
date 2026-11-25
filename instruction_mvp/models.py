from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SourceInput:
    instruction_type: str = "internal"
    audience: str = ""
    location: str = ""
    purpose: str = ""
    scenario: str = ""
    context: str = ""
    comments: str = ""
    free_form: bool = False

    def missing_required(self) -> list[str]:
        if self.free_form:
            # Свободный формат: полноту данных оценивает модель (STATUS: NEED_INFO),
            # форма требует только непустой ТЗ.
            return [] if self.context.strip() else ["ТЗ и контекст задачи"]
        fields = (
            ("audience", "Для кого инструкция (роль или группа)"),
            ("location", "Где находится функция в интерфейсе"),
            ("purpose", "Что делает функция и когда ей пользуются"),
            ("scenario", "Основной сценарий по шагам"),
            ("context", "ТЗ и контекст задачи"),
        )
        return [label for name, label in fields if not getattr(self, name).strip()]

    def as_source_text(self, previous_draft: str = "") -> str:
        sections = (
            ("Тип инструкции", self.instruction_type),
            ("Роль или группа", self.audience),
            ("Место в интерфейсе", self.location),
            ("Назначение", self.purpose),
            ("Основной сценарий", self.scenario),
            ("ТЗ и контекст", self.context),
            ("Замечания на доработку", self.comments),
            ("Текущий текст инструкции", previous_draft),
        )
        return "\n\n".join(
            f"{label}:\n{value.strip()}"
            for label, value in sections
            if value and value.strip()
        )


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: str = "warning"
    metric: str = ""


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    score: float
    weight: int
    details: str


@dataclass
class QualityReport:
    score: float
    recommendation: str
    metrics: list[Metric] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    unresolved_count: int = 0
    unsupported_ui: list[str] = field(default_factory=list)
    unsupported_urls: list[str] = field(default_factory=list)
    change_ratio: float | None = None
    similarity: float | None = None

    @property
    def critical_issues(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "critical"]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["critical_count"] = len(self.critical_issues)
        return result
