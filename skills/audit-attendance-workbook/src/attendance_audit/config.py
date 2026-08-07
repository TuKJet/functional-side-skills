from typing import Any


AUTHORIZED_ABSENCE_EVENTS = (
    "事假",
    "调休",
    "病假",
    "年假",
    "产假",
    "陪产假",
    "婚假",
    "丧假",
    "哺乳假",
    "出差",
    "外出",
)


DEFAULT_RULES: dict[str, Any] = {
    "schema": {
        "data_start_row": 5,
        "daily_start_column": "AG",
        "period_cell": "A1",
        "generated_at_cell": "A2",
        "attendance_days_column": "R",
        "attendance_days_header": "出勤天数",
        "summary_columns": {
            "T": "上班缺卡次数",
            "U": "下班缺卡次数",
            "V": "迟到次数",
            "W": "迟到时长",
            "X": "早退次数",
            "Y": "早退时长",
            "Z": "旷工天数",
        },
    },
    "screening": {
        "normal_fill_keys": [
            "none",
            "indexed:9",
            "rgb:FFFFFF",
            "rgb:00FFFFFF",
            "theme:0",
        ],
        "neighbor_days": 1,
        "agent_text_max_chars": 600,
        "explicit_anomaly_keywords": ["迟到", "早退", "缺卡", "旷工"],
    },
    "shifts": {
        "研发A班": {
            "id": "SHIFT_A",
            "mode": "fixed",
            "start": "09:00",
            "end": "18:00",
            "grace_minutes": 10,
        },
        "研发B班": {
            "id": "SHIFT_B",
            "mode": "fixed",
            "start": "12:00",
            "end": "21:00",
            "grace_minutes": 10,
        },
        "默认班次": {
            "id": "FLEX_SHIFT",
            "mode": "flexible",
        },
    },
    "events": [*AUTHORIZED_ABSENCE_EVENTS, "加班", "补卡申请"],
    "authorized_absence_events": AUTHORIZED_ABSENCE_EVENTS,
    "shift_misclassification": {
        "minimum_score_improvement": 35,
        "maximum_candidate_score": 70,
    },
    "attendance_alignment": {
        "missing_punch_deduction_days": 0.5,
        "workday_minutes": 450,
        "rounding_increment_days": 0.1,
        "shift_report_threshold": 30,
    },
}


def shift_by_id(rules: dict[str, Any], shift_id: str) -> tuple[str, dict[str, Any]] | None:
    for label, definition in rules["shifts"].items():
        if definition["id"] == shift_id:
            return label, definition
    return None
