"""Excel offline ledger export/import with optimistic concurrency checks."""

from __future__ import annotations

import json
import hashlib
import os
import uuid
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Font, PatternFill, Alignment

STATUSES = ["待筛选", "待复核", "初面", "二面", "待决定", "录用", "未录用", "已撤回"]
EDITABLE = ("id", "expected_version", "status", "owner", "notes", "next_action_date")
BASELINE_SHEET = "_台账基线"
CHECK_SHEET = "_台账校验"


def _safe(value: Any) -> Any:
    """Prevent Excel formula injection while retaining display text."""
    return "" if value is None else value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _baseline_digest(rows: list[list[Any]]) -> str:
    normalized = [["" if v is None else ((v.date().isoformat() if isinstance(v, datetime) else v.isoformat()) if isinstance(v, (date, datetime)) else v) for v in row] for row in rows]
    return hashlib.sha256(_json(normalized).encode("utf-8")).hexdigest()


def _date_value(value: Any) -> Any:
    """Store ISO dates as native Excel dates while leaving other values alone."""
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return _safe(value)
    return value


def _date_normalized(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")


def _rows(snapshot: dict) -> dict[str, list[dict]]:
    return {key: [dict(row) for row in snapshot.get(key, [])] for key in ("candidates", "jobs", "applications", "reviews", "interviews")}


def _resume_fields(row: dict) -> tuple[str, str, str]:
    resume = row.get("resume") or {}
    return str(resume.get("path", "")), str(resume.get("sha256", "")), str(resume.get("text_status", ""))


def export_ledger(snapshot: dict, path: Path) -> dict:
    """Create a formatted ledger. Existing files are never overwritten."""
    path = Path(path)
    if path.exists():
        raise ValueError(f"不会覆盖已有台账: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _rows(snapshot)
    wb = Workbook()
    overview = wb.active
    overview.title = "总览"
    overview.append(["HR 简历总台账", "离线导出，不支持多人同时编辑；请通过 Skill 回写"])
    overview.append(["数据版本", snapshot.get("revision", 0)])
    overview.append(["候选人", len(rows["candidates"])])
    overview.append(["招聘需求", len(rows["jobs"])])
    overview.append(["投递记录", len(rows["applications"])])
    overview.append(["审核记录", len(rows["reviews"])])
    overview.append(["二面评价", len(rows["interviews"])])
    overview.freeze_panes = "A2"
    _write_applications(wb, rows["applications"], rows)
    _write_candidates(wb, rows["candidates"])
    _write_jobs(wb, rows["jobs"])
    _write_reviews(wb, rows["reviews"])
    _write_interviews(wb, rows["interviews"])
    _write_ranking(wb, rows["reviews"], rows)
    instructions = wb.create_sheet("使用说明")
    instructions.append(["用途", "本文件是 Skill 生成的离线维护视图；原始简历和审核证据仍在共享目录。"])
    instructions.append(["可编辑字段", "投递台账中的 status、owner、notes、next_action_date；id 和 expected_version 仅用于回写校验。"])
    instructions.append(["回写", "保存后让 WorkBuddy 执行‘读取台账并回写投递状态’，若版本不一致请重新导出。"])
    instructions.append(["评审", "审核排序仅作辅助，必须由 HR 复核；过期审核记录应重新核对简历和 JD。"])
    instructions.append(["NAS", "将数据根配置为 NAS 共享目录；不要用个人副本互相覆盖。"])
    baseline = wb.create_sheet(BASELINE_SHEET)
    baseline.sheet_state = "hidden"
    baseline.append(["id", "version", "candidate_id", "job_id", "month", "status", "owner", "notes", "next_action_date", "candidate_name", "department", "title"])
    candidates = {x.get("id"): x for x in rows["candidates"]}
    jobs = {x.get("id"): x for x in rows["jobs"]}
    for app in rows["applications"]:
        candidate, job = candidates.get(app.get("candidate_id"), {}), jobs.get(app.get("job_id"), {})
        baseline.append([_date_value(app.get("next_action_date", "")) if k == "next_action_date" else _safe(app.get(k, "")) for k in ("id", "version", "candidate_id", "job_id", "month", "status", "owner", "notes", "next_action_date")] + [_safe(candidate.get("name", "")), _safe(job.get("department", "")), _safe(job.get("title", ""))])
    check = wb.create_sheet(CHECK_SHEET)
    check.sheet_state = "veryHidden"
    check.append(["baseline_sha256"])
    check.append([_baseline_digest([list(row) for row in baseline.iter_rows(min_row=2, values_only=True)])])
    for sheet in wb.worksheets:
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
        sheet.sheet_view.showGridLines = False
        for col in sheet.columns:
            letter = col[0].column_letter
            sheet.column_dimensions[letter].width = min(60, max(12, max(len(str(c.value or "")) for c in col) + 2))
    # Convert all untrusted strings to explicit text cells, including formula-like input.
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        wb.save(temporary)
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise ValueError(f"不会覆盖已有台账: {path}") from None
        except PermissionError:
            # Some SMB providers and the Windows sandbox disallow hard links;
            # exclusive creation still guarantees that an existing file cannot
            # be replaced by this export.
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                with os.fdopen(descriptor, "wb") as target, temporary.open("rb") as source:
                    target.write(source.read())
            except FileExistsError:
                raise ValueError(f"不会覆盖已有台账: {path}") from None
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(path), "rows": {key: len(value) for key, value in rows.items()}, "revision": snapshot.get("revision", 0)}


def _write_applications(wb: Workbook, apps: list[dict], rows: dict[str, list[dict]]) -> None:
    sheet = wb.create_sheet("投递台账")
    headers = ["ID", "版本", "候选人ID", "岗位ID", "月份", "状态", "负责人", "备注", "下一步日期", "候选人", "部门", "岗位"]
    keys = ("id", "version", "candidate_id", "job_id", "month", "status", "owner", "notes", "next_action_date")
    sheet.append(headers)
    candidates = {x.get("id"): x for x in rows["candidates"]}
    jobs = {x.get("id"): x for x in rows["jobs"]}
    for app in apps:
        candidate, job = candidates.get(app.get("candidate_id"), {}), jobs.get(app.get("job_id"), {})
        sheet.append([_date_value(app.get(k, "")) if k == "next_action_date" else _safe(app.get(k, "")) for k in keys] + [_safe(candidate.get("name", "")), _safe(job.get("department", "")), _safe(job.get("title", ""))])
        sheet.cell(sheet.max_row, 9).number_format = "yyyy-mm-dd"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
    sheet.add_data_validation(dv)
    dv.add(f"F2:F{max(2, len(apps)+1)}")
    fill = PatternFill("solid", fgColor="FFF2CC")
    for row in sheet.iter_rows(min_row=2, max_col=9):
        for cell in row:
            if cell.column in (6, 7, 8, 9):
                cell.fill = fill
    sheet.conditional_formatting.add(f"F2:F{max(2, len(apps)+1)}", FormulaRule(formula=['F2="待复核"'], fill=PatternFill("solid", fgColor="FCE4D6")))


def _write_candidates(wb: Workbook, candidates: list[dict]) -> None:
    sheet = wb.create_sheet("候选人")
    sheet.append(["候选人ID", "版本", "姓名", "来源", "备注", "简历位置", "简历校验值", "文本提取状态"])
    for row in candidates:
        path, sha, status = _resume_fields(row)
        sheet.append([_safe(row.get(k, "")) for k in ("id", "version", "name", "source", "notes")] + [_safe(path), _safe(sha), _safe(status)])
    sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions


def _write_jobs(wb: Workbook, jobs: list[dict]) -> None:
    sheet = wb.create_sheet("招聘需求")
    sheet.append(["岗位ID", "版本", "部门", "岗位", "招聘月份", "需求人数", "岗位状态", "岗位说明", "评分要求详情"])
    for row in jobs:
        sheet.append([_safe(row.get(k, "")) for k in ("id", "version", "department", "title", "month", "headcount", "status", "description")] + [_safe(_json(row.get("criteria", [])))])
    sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions


def _write_reviews(wb: Workbook, reviews: list[dict]) -> None:
    sheet = wb.create_sheet("审核记录")
    sheet.append(["ID", "投递ID", "JD版本", "简历SHA256", "匹配分", "证据覆盖率", "必备项缺口", "优势", "风险", "追问", "已过期", "审核人", "逐项审核JSON"])
    for row in reviews:
        sheet.append([_safe(row.get("id", "")), _safe(row.get("application_id", "")), _safe(row.get("job_version", "")), _safe(row.get("resume_sha256", "")), _safe(row.get("score", "")), _safe(row.get("coverage", "")), _safe(_json(row.get("required_gaps", []))), _safe(_json(row.get("strengths", []))), _safe(_json(row.get("risks", []))), _safe(_json(row.get("questions", []))), _safe(row.get("is_stale", False)), _safe(row.get("reviewer", "")), _safe(_json(row.get("criteria", [])))])
    sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions


def _write_ranking(wb: Workbook, reviews: list[dict], rows: dict[str, list[dict]]) -> None:
    sheet = wb.create_sheet("岗位排序")
    sheet.append(["投递ID", "候选人", "部门", "岗位", "月份", "匹配分", "证据覆盖率", "必备项缺口", "排序状态"])
    candidates = {x.get("id"): x for x in rows["candidates"]}
    jobs = {x.get("id"): x for x in rows["jobs"]}
    by_app = {x.get("id"): x for x in rows["applications"]}
    entries = []
    latest = {}
    for review in reviews:
        if not review.get('is_stale'):
            latest[review.get('application_id')] = review
    for review in latest.values():
        app = by_app.get(review.get("application_id"), {})
        job = jobs.get(app.get("job_id"), {})
        candidate = candidates.get(app.get("candidate_id"), {})
        if review.get("is_stale") or review.get("score") is None:
            continue
        entries.append((job.get("id", ""), -float(review["score"]), review.get("application_id", ""), app, job, candidate, review))
    for _, _, _, app, job, candidate, review in sorted(entries):
        sheet.append([_safe(app.get("id", "")), _safe(candidate.get("name", "")), _safe(job.get("department", "")), _safe(job.get("title", "")), _safe(job.get("month", "")), _safe(review.get("score", "")), _safe(review.get("coverage", "")), _safe(_json(review.get("required_gaps", []))), "有效审核，供 HR 复核"])
    sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions


def _write_interviews(wb: Workbook, interviews: list[dict]) -> None:
    sheet = wb.create_sheet("面试评价")
    sheet.append(["评价ID", "投递ID", "面试轮次", "评价人", "评价摘要", "建议", "评价表位置", "评价表校验值", "面试日期"])
    for row in interviews:
        attachment = row.get("attachment") or {}
        sheet.append([_safe(row.get(k, "")) for k in ("id", "application_id", "round", "evaluator", "summary", "recommendation")] + [_safe(attachment.get("path", "")), _safe(attachment.get("sha256", "")), _safe(row.get("interview_date", ""))])
    sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions


def read_ledger(path: Path) -> list[dict]:
    """Read only approved application edits after validating hidden baselines."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"台账不存在: {path}")
    wb = load_workbook(path, data_only=False, read_only=False)
    if "投递台账" not in wb.sheetnames or BASELINE_SHEET not in wb.sheetnames:
        raise ValueError("不是 HR 简历台账，缺少投递台账或基线")
    sheet, baseline = wb["投递台账"], wb[BASELINE_SHEET]
    if CHECK_SHEET not in wb.sheetnames or wb[CHECK_SHEET].sheet_state != "veryHidden":
        raise ValueError("台账校验元数据缺失或被显示")
    headers = [cell.value for cell in sheet[1]]
    required = ["ID", "版本", "候选人ID", "岗位ID", "月份", "状态", "负责人", "备注", "下一步日期"]
    if headers[:9] != required:
        raise ValueError("投递台账列结构已被修改")
    base_headers = [cell.value for cell in baseline[1]]
    expected_base = ["id", "version", "candidate_id", "job_id", "month", "status", "owner", "notes", "next_action_date", "candidate_name", "department", "title"]
    if base_headers != expected_base:
        raise ValueError("台账基线结构已被修改")
    base_map: dict[str, list[Any]] = {}
    baseline_rows = []
    for row in baseline.iter_rows(min_row=2, values_only=True):
        baseline_rows.append(list(row))
        if not row[0] or str(row[0]) in base_map:
            raise ValueError("台账基线存在空 ID 或重复 ID")
        base_map[str(row[0])] = list(row)
    if wb[CHECK_SHEET]["A2"].value != _baseline_digest(baseline_rows):
        raise ValueError("隐藏基线完整性校验失败，请重新导出台账")
    seen = set(); changes = []
    for row_index, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if all(value in (None, "") for value in row):
            continue
        current = list(row)
        if len(current) < 12:
            raise ValueError(f"投递台账第 {row_index} 行列数不足")
        if any(cell.data_type == "f" for cell in sheet[row_index][:12]):
            raise ValueError(f"投递台账第 {row_index} 行包含公式，拒绝回写")
        identifier = str(current[0] or "")
        if not identifier or identifier in seen:
            raise ValueError(f"投递台账第 {row_index} 行 ID 为空或重复")
        seen.add(identifier)
        if identifier not in base_map:
            raise ValueError(f"不允许新增投递记录: {identifier}")
        baseline_row = base_map[identifier]
        # Read-only identifiers and relationships must remain exactly unchanged.
        for index, label in enumerate(("version", "candidate_id", "job_id", "month"), start=1):
            if str(current[index] or "") != str(baseline_row[index] or ""):
                raise ValueError(f"{identifier} 的 {label} 不可编辑")
        for index, label in ((9, "候选人"), (10, "部门"), (11, "岗位")):
            if str(current[index] or "") != str(baseline_row[index] or ""):
                raise ValueError(f"{identifier} 的 {label} 展示字段不可编辑")
        if current[5] not in (None, "") and current[5] not in STATUSES:
            raise ValueError(f"{identifier} 状态无效: {current[5]}")
        try:
            expected = int(current[1])
        except (TypeError, ValueError):
            raise ValueError(f"{identifier} expected_version 必须是整数")
        edit = {"id": identifier, "expected_version": expected, "status": current[5] or "", "owner": current[6] or "", "notes": current[7] or "", "next_action_date": _date_normalized(current[8])}
        baseline_edit = {"status": baseline_row[5] or "", "owner": baseline_row[6] or "", "notes": baseline_row[7] or "", "next_action_date": _date_normalized(baseline_row[8])}
        if any(edit[key] != baseline_edit[key] for key in baseline_edit):
            changes.append(edit)
    if seen != set(base_map):
        raise ValueError("投递台账行被删除或基线不一致")
    # Any extra visible columns are read-only; compare against baseline-linked fields where possible.
    return changes
