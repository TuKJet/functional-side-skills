from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AttendanceEvent:
    name: str


@dataclass
class ParsedCell:
    raw: str
    body: str
    punch_suffix: str
    punches: list[str]
    shift_label: str | None
    shift_id: str | None
    shift_status: str | None
    base_state: str | None
    events: list[AttendanceEvent] = field(default_factory=list)
    unknown_tokens: list[str] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return not self.unknown_tokens

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["supported"] = self.supported
        return data


@dataclass
class Finding:
    rule_id: str
    category: str
    severity: str
    message: str
    needs_human_review: bool
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Assessment:
    source_cell: str
    row: int
    column: int
    date: str
    person: dict[str, Any]
    parsed: ParsedCell
    corrected_text: str
    screening_reasons: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    review_item: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_cell": self.source_cell,
            "row": self.row,
            "column": self.column,
            "date": self.date,
            "person": self.person,
            "parsed": self.parsed.to_dict(),
            "corrected_text": self.corrected_text,
            "screening_reasons": self.screening_reasons,
            "findings": [finding.to_dict() for finding in self.findings],
            "review_item": self.review_item,
        }
