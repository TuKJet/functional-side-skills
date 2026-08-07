from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from .config import shift_by_id
from .models import Assessment, Finding, ParsedCell
from .parser import replace_base_state, replace_shift_and_status


ABNORMAL_KEYWORDS = ("迟到", "早退", "缺卡", "旷工")


def time_to_minutes(value: str) -> int:
    hours, minutes = value.split(":", 1)
    return int(hours) * 60 + int(minutes)


def _score_shift(punch_in: int, punch_out: int, definition: dict[str, Any]) -> float:
    start = time_to_minutes(definition["start"])
    end = time_to_minutes(definition["end"])
    if punch_out < punch_in:
        punch_out += 24 * 60
    start_penalty = (start - punch_in) * 0.25 if punch_in < start else punch_in - start
    end_penalty = (punch_out - end) * 0.10 if punch_out > end else end - punch_out
    return round(max(0, start_penalty) + max(0, end_penalty), 2)


def _desired_status(
    definition: dict[str, Any],
    punches: list[str],
    record_date: datetime,
    generated_at: datetime,
) -> tuple[str | None, list[tuple[str, int]]]:
    start = time_to_minutes(definition["start"])
    end = time_to_minutes(definition["end"])
    grace = int(definition.get("grace_minutes", 0))
    report_day_open = record_date.date() == generated_at.date() and (
        generated_at.hour * 60 + generated_at.minute < end
    )

    if not punches:
        if report_day_open:
            return None, []
        return "旷工", [("ABSENCE", 1)]

    punch_minutes = [time_to_minutes(value) for value in punches]
    if len(punch_minutes) == 1:
        if report_day_open:
            return None, []
        midpoint = (start + end) // 2
        if punch_minutes[0] <= midpoint:
            return "下班缺卡", [("MISSING_OUT", 1)]
        return "上班缺卡", [("MISSING_IN", 1)]

    punch_in, punch_out = punch_minutes[0], punch_minutes[-1]
    if punch_out < punch_in:
        punch_out += 24 * 60
    delay = max(0, punch_in - start)
    late = max(0, delay - grace)
    required_out = end + delay
    early = 0 if report_day_open else max(0, required_out - punch_out)
    anomalies: list[tuple[str, int]] = []
    if late:
        anomalies.append(("LATE", late))
    if early:
        anomalies.append(("EARLY", early))
    if late and early:
        return f"上班迟到{late}分钟且早退{early}分钟", anomalies
    if late:
        return f"上班迟到{late}分钟", anomalies
    if early:
        return f"早退{early}分钟", anomalies
    return "正常", anomalies


def _finding(
    rule_id: str,
    category: str,
    message: str,
    confidence: float = 1.0,
    *,
    needs_human_review: bool = True,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        category=category,
        severity="高" if category in {"旷工", "缺卡"} else "中",
        message=message,
        needs_human_review=needs_human_review,
        confidence=confidence,
    )


def _review_id(source_cell: str, rule_id: str) -> str:
    return f"{source_cell}:{rule_id}"


def _compact_text(value: str, limit: int) -> str:
    value = value.strip()
    return value if len(value) <= limit else f"{value[:limit]}…"


def _neighbor_context(
    records: list[dict[str, Any]], index: int, radius: int
) -> list[dict[str, Any]]:
    context: list[dict[str, Any]] = []
    start = max(0, index - radius)
    end = min(len(records), index + radius + 1)
    for neighbor_index in range(start, end):
        if neighbor_index == index:
            continue
        neighbor = records[neighbor_index]
        parsed: ParsedCell = neighbor["parsed"]
        context.append(
            {
                "date": neighbor["date"].date().isoformat(),
                "shift_id": parsed.shift_id,
                "shift_status": parsed.shift_status,
                "first_punch": parsed.punches[0] if parsed.punches else None,
                "last_punch": parsed.punches[-1] if parsed.punches else None,
            }
        )
    return context


def assess_record(
    record: dict[str, Any],
    records_for_person: list[dict[str, Any]],
    record_index: int,
    rules: dict[str, Any],
    generated_at: datetime,
) -> Assessment:
    parsed: ParsedCell = record["parsed"]
    findings: list[Finding] = []
    corrected_text = parsed.raw
    review_item: dict[str, Any] | None = None

    assessment = Assessment(
        source_cell=record["source_cell"],
        row=record["row"],
        column=record["column"],
        date=record["date"].date().isoformat(),
        person=record["person"],
        parsed=parsed,
        corrected_text=corrected_text,
        screening_reasons=list(record.get("screening_reasons", [])),
    )

    if not parsed.supported:
        finding = _finding(
            "PARSE_UNSUPPORTED",
            "未识别文本",
            "Python 解析器无法完整识别该考勤文本，需要 Agent 结合原文判断。",
            0.0,
        )
        findings.append(finding)
        review_item = {
            "review_id": _review_id(record["source_cell"], finding.rule_id),
            "review_type": "unsupported_text",
            "source_cell": record["source_cell"],
            "date": assessment.date,
            "raw_text": _compact_text(
                parsed.raw,
                int(rules["screening"]["agent_text_max_chars"]),
            ),
            "unknown_tokens": [_compact_text(token, 120) for token in parsed.unknown_tokens[:3]],
            "known_fields": {
                "shift_id": parsed.shift_id,
                "shift_status": parsed.shift_status,
                "base_state": parsed.base_state,
                "punches": parsed.punches,
            },
            "allowed_actions": ["keep", "replace"],
        }
        assessment.findings = findings
        assessment.review_item = review_item
        return assessment

    event_names = {event.name for event in parsed.events}
    authorized_events = event_names.intersection(rules["authorized_absence_events"])

    if parsed.base_state in {"未排班", "未排班并打卡", "休息"}:
        if parsed.punches and parsed.base_state == "未排班":
            corrected_text = replace_base_state(parsed, "未排班并打卡")
            findings.append(
                _finding(
                    "UNSCHEDULED_PUNCH",
                    "未排班打卡",
                    "未排班日存在打卡记录。",
                )
            )
        elif parsed.punches and parsed.base_state == "未排班并打卡" and "加班" not in event_names:
            findings.append(
                _finding(
                    "UNSCHEDULED_PUNCH_REVIEW",
                    "未排班打卡",
                    "未排班日存在打卡且未发现加班记录，需要人工确认。",
                    0.8,
                )
            )
        assessment.corrected_text = corrected_text
        assessment.findings = findings
        return assessment

    if parsed.shift_id is None:
        assessment.corrected_text = corrected_text
        assessment.findings = findings
        return assessment

    shift_entry = shift_by_id(rules, parsed.shift_id)
    if shift_entry is None:
        assessment.findings = findings
        return assessment
    shift_label, definition = shift_entry

    if definition["mode"] == "flexible":
        if parsed.shift_status and any(key in parsed.shift_status for key in ABNORMAL_KEYWORDS):
            findings.append(
                _finding(
                    "FLEX_EXPLICIT_ANOMALY",
                    "弹性班次异常",
                    "弹性班次不执行固定时间计算，但原记录包含显式异常，需要人工审查。",
                    0.8,
                )
            )
        assessment.findings = findings
        return assessment

    if authorized_events:
        if parsed.shift_status and any(key in parsed.shift_status for key in ABNORMAL_KEYWORDS):
            findings.append(
                _finding(
                    "APPROVAL_WITH_ANOMALY",
                    "审批与异常并存",
                    "该日同时存在审批事件和显式考勤异常，需要人工确认审批覆盖范围。",
                    0.7,
                )
            )
        assessment.findings = findings
        return assessment

    desired_status, anomalies = _desired_status(
        definition,
        parsed.punches,
        record["date"],
        generated_at,
    )

    if len(parsed.punches) >= 2:
        punch_in = time_to_minutes(parsed.punches[0])
        punch_out = time_to_minutes(parsed.punches[-1])
        assigned_score = _score_shift(punch_in, punch_out, definition)
        candidates: list[tuple[float, str, str, dict[str, Any]]] = []
        for other_label, other_definition in rules["shifts"].items():
            if other_definition.get("mode") != "fixed" or other_definition["id"] == parsed.shift_id:
                continue
            score = _score_shift(punch_in, punch_out, other_definition)
            candidates.append((score, other_label, other_definition["id"], other_definition))
        if candidates:
            best_score, best_label, best_id, best_definition = min(
                candidates, key=lambda item: item[0]
            )
            improvement = assigned_score - best_score
            settings = rules["shift_misclassification"]
            if improvement >= float(settings["minimum_score_improvement"]) and best_score <= float(
                settings["maximum_candidate_score"]
            ):
                proposed_status, _ = _desired_status(
                    best_definition,
                    parsed.punches,
                    record["date"],
                    generated_at,
                )
                proposed_text = replace_shift_and_status(
                    parsed,
                    best_label,
                    proposed_status or parsed.shift_status or "正常",
                )
                source_has_timing_anomaly = bool(
                    parsed.shift_status
                    and any(key in parsed.shift_status for key in ("迟到", "早退"))
                )
                assigned_has_timing_anomaly = bool(
                    desired_status and any(key in desired_status for key in ("迟到", "早退"))
                )
                auto_correct = (
                    proposed_status == "正常"
                    and source_has_timing_anomaly
                    and assigned_has_timing_anomaly
                )
                if auto_correct:
                    findings.append(
                        _finding(
                            "SHIFT_MISCLASSIFICATION_AUTO",
                            "班次错排已矫正",
                            "原班次产生迟到或早退，但完整打卡更符合另一固定班次。",
                            min(0.99, 0.75 + improvement / 400),
                            needs_human_review=False,
                        )
                    )
                    assessment.corrected_text = proposed_text
                    assessment.findings = findings
                    return assessment

                finding = _finding(
                    "SHIFT_MISCLASSIFICATION",
                    "疑似班次错分",
                    "打卡更接近另一固定班次，但未满足自动改班条件，需要短上下文复核。",
                    min(0.99, 0.5 + improvement / 200),
                )
                findings.append(finding)
                review_item = {
                    "review_id": _review_id(record["source_cell"], finding.rule_id),
                    "review_type": "shift_misclassification",
                    "source_cell": record["source_cell"],
                    "date": assessment.date,
                    "parsed": {
                        "assigned_shift_id": parsed.shift_id,
                        "assigned_status": parsed.shift_status,
                        "punches": parsed.punches,
                    },
                    "candidate": {
                        "shift_id": best_id,
                        "assigned_score": assigned_score,
                        "candidate_score": best_score,
                        "score_improvement": round(improvement, 2),
                        "proposed_text": proposed_text,
                    },
                    "neighbor_context": _neighbor_context(
                        records_for_person,
                        record_index,
                        int(rules["screening"]["neighbor_days"]),
                    ),
                    "allowed_actions": ["keep", "accept_suggestion", "replace"],
                }
                assessment.findings = findings
                assessment.review_item = review_item
                return assessment

    if desired_status is not None and desired_status != parsed.shift_status:
        corrected_text = replace_shift_and_status(parsed, shift_label, desired_status)

    for anomaly_type, minutes in anomalies:
        if anomaly_type == "LATE":
            findings.append(_finding("LATE", "迟到", f"超过宽限时间，计算迟到 {minutes} 分钟。"))
        elif anomaly_type == "EARLY":
            findings.append(_finding("EARLY", "早退", f"未达到顺延下班时间，早退 {minutes} 分钟。"))
        elif anomaly_type == "MISSING_IN":
            findings.append(_finding("MISSING_IN", "缺卡", "缺少上班打卡。"))
        elif anomaly_type == "MISSING_OUT":
            findings.append(_finding("MISSING_OUT", "缺卡", "缺少下班打卡。"))
        elif anomaly_type == "ABSENCE":
            findings.append(_finding("ABSENCE", "旷工", "无有效打卡或审批覆盖，判定为旷工。"))

    if (
        not findings
        and parsed.shift_status
        and any(key in parsed.shift_status for key in ABNORMAL_KEYWORDS)
    ):
        findings.append(
            _finding(
                "STATUS_MISMATCH",
                "状态不一致",
                "原记录包含异常状态，但按当前规则计算未发现对应异常，已矫正并保留审查记录。",
                0.8,
            )
        )

    assessment.corrected_text = corrected_text
    assessment.findings = findings
    assessment.review_item = review_item
    return assessment


def extract_metric(status: str | None, keyword: str) -> int:
    if not status:
        return 0
    match = re.search(rf"{re.escape(keyword)}(\d+)分钟", status)
    return int(match.group(1)) if match else 0


def finite_confidence(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default
