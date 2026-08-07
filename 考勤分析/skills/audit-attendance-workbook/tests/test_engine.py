from datetime import datetime, timedelta

from attendance_audit.config import DEFAULT_RULES
from attendance_audit.engine import assess_record
from attendance_audit.parser import parse_cell


def _record(raw: str, date: datetime, column: int, rules: dict) -> dict:
    return {
        "source_cell": f"Sheet1!{column}",
        "row": 5,
        "column": column,
        "date": date,
        "person": {},
        "parsed": parse_cell(raw, rules),
    }


def test_decisive_shift_b_error_is_corrected_without_agent() -> None:
    rules = DEFAULT_RULES
    date = datetime(2026, 7, 2)
    records = [
        _record("研发B班:正常\n(11:50,21:05)", date - timedelta(days=1), 33, rules),
        _record("研发A班:上班迟到50分钟\n(10:00,21:00)", date, 34, rules),
        _record("研发B班:正常\n(11:55,21:02)", date + timedelta(days=1), 35, rules),
    ]

    assessment = assess_record(records[1], records, 1, rules, datetime(2026, 7, 4, 12, 0))

    assert assessment.review_item is None
    assert assessment.corrected_text == "研发B班:正常\n(10:00,21:00)"
    assert assessment.findings[0].rule_id == "SHIFT_MISCLASSIFICATION_AUTO"
    assert not assessment.findings[0].needs_human_review


def test_ambiguous_shift_candidate_uses_short_agent_context() -> None:
    rules = DEFAULT_RULES
    date = datetime(2026, 7, 2)
    records = [
        _record("研发B班:正常\n(11:50,21:05)", date - timedelta(days=1), 33, rules),
        _record("研发A班:正常\n(10:00,21:00)", date, 34, rules),
        _record("研发B班:正常\n(11:55,21:02)", date + timedelta(days=1), 35, rules),
    ]

    assessment = assess_record(records[1], records, 1, rules, datetime(2026, 7, 4, 12, 0))

    assert assessment.review_item is not None
    assert assessment.review_item["review_type"] == "shift_misclassification"
    assert len(assessment.review_item["neighbor_context"]) <= 2
    assert "raw_text" not in assessment.review_item


def test_open_report_day_does_not_create_missing_checkout() -> None:
    rules = DEFAULT_RULES
    date = datetime(2026, 7, 27)
    records = [_record("研发A班:正常\n(09:07,-)", date, 33, rules)]

    assessment = assess_record(records[0], records, 0, rules, datetime(2026, 7, 27, 11, 24))

    assert assessment.findings == []
    assert assessment.corrected_text == "研发A班:正常\n(09:07,-)"
