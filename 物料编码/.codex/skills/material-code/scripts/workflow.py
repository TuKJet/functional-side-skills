# -*- coding: utf-8 -*-
"""Codex entry point for the material-code workflow.

This wrapper keeps each application in an isolated job, makes mutating actions
explicit, and restores the database/result artifact when an underlying command
fails or partially skips work.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from project_paths import find_project_root
from workflow_guard import INTERNAL_CALL_ENV, INTERNAL_CALL_VALUE


SKILL_VERSION = 2
SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent
JOBS_REL = Path("output/material-code-jobs")
BACKUPS_REL = Path("output/material-code-backups")
DATABASE_CONFIRMATION_REL = JOBS_REL / "database-confirmation.json"


def fail(message: str, code: int = 2) -> None:
    print(f"[错误] {message}", file=sys.stderr)
    raise SystemExit(code)


def project_python(root: Path) -> Path:
    return Path(sys.executable).resolve()


def db_path(root: Path) -> Path:
    return root / "material_codes.db"


def script_path(root: Path, name: str) -> Path:
    path = SKILL_SCRIPTS_DIR / name
    if not path.is_file():
        fail(f"工程脚本不存在: {path}")
    return path


def run_child(root: Path, script: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [str(project_python(root)), str(script_path(root, script)), *args]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env[INTERNAL_CALL_ENV] = INTERNAL_CALL_VALUE
    proc = subprocess.run(
        cmd,
        cwd=root,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.stdout:
        output = proc.stdout
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        output = output.encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
        print(output, end="" if output.endswith("\n") else "\n")
    return proc


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取状态文件 {path}: {exc}")
    raise AssertionError


def unique_job_dir(root: Path, application: Path) -> Path:
    safe_stem = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", application.stem).strip("_")
    safe_stem = safe_stem or "application"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = root / JOBS_REL / f"{safe_stem}-{stamp}"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = Path(f"{base}-{suffix}")
        suffix += 1
    return candidate


def active_pointer(root: Path) -> Path:
    return root / JOBS_REL / "active.json"


def database_confirmation_path(root: Path) -> Path:
    return root / DATABASE_CONFIRMATION_REL


def resolve_job(root: Path, job_arg: str | None) -> tuple[Path, dict[str, Any]]:
    if job_arg:
        requested = Path(job_arg)
        if not requested.is_absolute():
            requested = root / requested
        manifest_path = requested / "job.json" if requested.is_dir() else requested
    else:
        pointer = active_pointer(root)
        if not pointer.is_file():
            fail("没有活动 job。先运行 prepare <申请表.xlsx>。")
        pointer_data = read_json(pointer)
        manifest_path = Path(pointer_data["manifest"])
    manifest_path = manifest_path.resolve()
    manifest = read_json(manifest_path)
    if Path(manifest.get("project_root", "")).resolve() != root.resolve():
        fail(f"job 不属于当前工程: {manifest_path}")
    if manifest.get("invalidated_by_sync"):
        fail(
            f"job 已因同步失效（{manifest['invalidated_by_sync']}）。"
            "请对申请表重新运行 prepare。"
        )
    expected_db_hash = (
        manifest.get("database_confirmation", {})
        .get("database", {})
        .get("sha256")
    )
    manifest_db = Path(manifest.get("db", ""))
    if not expected_db_hash or not manifest_db.is_file():
        fail("job 缺少有效的数据库指纹；必须重新确认数据库来源并 prepare。")
    if file_sha256(manifest_db) != expected_db_hash:
        fail("数据库在 prepare 后发生变化；旧候选和结果写入授权全部失效，必须重新 prepare。")
    return manifest_path, manifest


def update_manifest(path: Path, manifest: dict[str, Any], **changes: Any) -> None:
    manifest.update(changes)
    manifest["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    atomic_json(path, manifest)


def database_meta(db: Path) -> dict[str, Any]:
    if not db.is_file():
        return {"exists": False, "rows": 0}
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        rows = con.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
        main = con.execute(
            "SELECT COUNT(*) FROM materials WHERE retired=0"
        ).fetchone()[0]
        retired = rows - main
        meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
        con.close()
        return {
            "exists": True,
            "rows": rows,
            "main": main,
            "retired": retired,
            "source_excel": meta.get("source_excel", ""),
            "synced_at": meta.get("synced_at", ""),
            "modified_at": dt.datetime.fromtimestamp(db.stat().st_mtime).isoformat(
                timespec="seconds"
            ),
        }
    except (sqlite3.Error, OSError) as exc:
        return {"exists": True, "error": str(exc), "rows": 0}


def backup_database(source: Path, destination: Path) -> None:
    if not source.is_file():
        fail(f"数据库不存在，无法备份: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(destination))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def snapshot_for_write(db: Path, result: Path, backup_dir: Path) -> dict[str, Any]:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir.mkdir(parents=True, exist_ok=True)
    db_backup = backup_dir / f"db-before-{stamp}.sqlite"
    backup_database(db, db_backup)
    result_existed = result.is_file()
    result_backup = backup_dir / f"result-before-{stamp}.xlsx"
    if result_existed:
        shutil.copy2(result, result_backup)
    return {
        "db": db,
        "db_backup": db_backup,
        "result": result,
        "result_existed": result_existed,
        "result_backup": result_backup if result_existed else None,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database_confirmation_identity(db: Path, info: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(db.resolve()),
        "sha256": file_sha256(db),
        "rows": int(info.get("rows") or 0),
        "source_excel": info.get("source_excel") or "",
        "synced_at": info.get("synced_at") or "",
    }


def database_confirmation_problem(
    confirmation: dict[str, Any], db: Path, info: dict[str, Any]
) -> str | None:
    if confirmation.get("invalidated_reason"):
        return f"该确认已失效：{confirmation['invalidated_reason']}"
    if confirmation.get("consumed_by"):
        return (
            "该确认已被上一份申请使用："
            f"{confirmation['consumed_by']}"
        )
    if not info.get("exists") or not info.get("rows") or info.get("error"):
        return "当前数据库不存在、为空或无法读取"
    if confirmation.get("decision") not in {"use_existing", "synced_new_master"}:
        return "确认记录中的用户选择无效"
    expected = confirmation.get("database") or {}
    expected_path = expected.get("path")
    if not isinstance(expected_path, str) or not expected_path:
        return "确认记录缺少数据库路径"
    recorded_path = os.path.normcase(str(Path(expected_path).resolve()))
    current_path = os.path.normcase(str(db.resolve()))
    if recorded_path != current_path:
        return "确认记录指向的数据库路径与当前工程不一致"
    if expected.get("sha256") != file_sha256(db):
        return "数据库内容在确认后发生了变化"
    return None


def database_confirmation_guidance() -> str:
    return (
        "每份新申请开始前必须先询问用户选择数据库来源。"
        "若用户明确回复沿用现有数据库，运行 use-existing --confirmed；"
        "若用户提供新总表，运行 sync <总表.xlsx> --confirmed。"
    )


def record_database_confirmation(
    root: Path, db: Path, decision: str, master_excel: Path | None = None
) -> tuple[Path, dict[str, Any]]:
    info = database_meta(db)
    if not info.get("exists") or not info.get("rows") or info.get("error"):
        fail("数据库不存在、为空或无法读取，不能记录数据库选择。")
    payload = {
        "version": 1,
        "decision": decision,
        "confirmed_at": dt.datetime.now().isoformat(timespec="seconds"),
        "master_excel": str(master_excel.resolve()) if master_excel else None,
        "database": database_confirmation_identity(db, info),
        "consumed_at": None,
        "consumed_by": None,
    }
    path = database_confirmation_path(root)
    atomic_json(path, payload)
    return path, payload


def require_database_confirmation(
    root: Path, db: Path, info: dict[str, Any]
) -> tuple[Path, dict[str, Any]]:
    path = database_confirmation_path(root)
    if not path.is_file():
        fail(f"数据库来源尚未获得用户确认。{database_confirmation_guidance()}")
    confirmation = read_json(path)
    problem = database_confirmation_problem(confirmation, db, info)
    if problem:
        fail(f"数据库来源确认无效：{problem}。{database_confirmation_guidance()}")
    return path, confirmation


def consume_database_confirmation(
    path: Path, confirmation: dict[str, Any], manifest_path: Path
) -> None:
    confirmation.update(
        {
            "consumed_at": dt.datetime.now().isoformat(timespec="seconds"),
            "consumed_by": str(manifest_path.resolve()),
        }
    )
    atomic_json(path, confirmation)


def invalidate_database_confirmation(root: Path, reason: str) -> None:
    path = database_confirmation_path(root)
    if not path.is_file():
        return
    confirmation = read_json(path)
    confirmation.update(
        {
            "invalidated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "invalidated_reason": reason,
        }
    )
    atomic_json(path, confirmation)


def restore_snapshot(snapshot: dict[str, Any], restore_db: bool = True) -> None:
    if restore_db:
        shutil.copy2(snapshot["db_backup"], snapshot["db"])
    result: Path = snapshot["result"]
    if snapshot["result_existed"]:
        shutil.copy2(snapshot["result_backup"], result)
    elif result.exists():
        result.unlink()


def command_failed(proc: subprocess.CompletedProcess[str]) -> bool:
    text = proc.stdout or ""
    markers = ("[错误]", "[跳过]", "[无选择]", "Traceback (most recent call last)")
    return proc.returncode != 0 or any(marker in text for marker in markers)


def process_args(manifest: dict[str, Any]) -> list[str]:
    return [
        str(manifest["application"]),
        "--db",
        str(manifest["db"]),
        "--cache",
        str(manifest["cache"]),
        "--result",
        str(manifest["result"]),
    ]


def load_cache(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    data = read_json(Path(manifest["cache"]))
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        fail("候选缓存格式无效，请重新 prepare。")
    return items


def processed_item_set(manifest: dict[str, Any]) -> set[int]:
    return {int(value) for value in manifest.get("processed_items", [])}


def next_pending_item(manifest: dict[str, Any], after: int = 0) -> int | None:
    processed = processed_item_set(manifest)
    for item in load_cache(manifest):
        idx = int(item["idx"])
        if idx > after and idx not in processed:
            return idx
    return None


def print_next_step(manifest: dict[str, Any], after: int = 0) -> None:
    next_item = next_pending_item(manifest, after)
    if next_item is None:
        print("[下一步] 没有待确认项目；本 job 已全部处理。")
    else:
        print(f"[下一步] workflow.py show {next_item}")


def validate_picks(items: list[dict[str, Any]], specs: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[int] = set()
    for spec in specs:
        match = re.fullmatch(r"(\d+):(0|[1-9]\d*|blank|不确定|空)", spec.strip())
        if not match:
            fail(f"选择格式无效: {spec}；应为 项号:候选号，例如 3:2 或 3:0。")
        idx = int(match.group(1))
        raw_pick = match.group(2)
        pick = 0 if raw_pick in {"0", "blank", "不确定", "空"} else int(raw_pick)
        if idx in seen:
            fail(f"同一命令重复指定第 {idx} 项。")
        if idx < 1 or idx > len(items):
            fail(f"第 {idx} 项超出范围，共 {len(items)} 项。")
        candidates = items[idx - 1].get("candidates") or []
        if pick and pick > len(candidates):
            fail(f"第 {idx} 项只有 {len(candidates)} 个候选，不能选择 {pick}。")
        seen.add(idx)
        normalized.append(f"{idx}:{pick}")
    if not normalized:
        fail("没有可提交的选择。")
    return normalized


def execute_job_write(
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    extra_args: list[str],
    operation: str,
    processed_items: list[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = Path(manifest["result"])
    snapshot = snapshot_for_write(
        Path(manifest["db"]), result, manifest_path.parent / "backups"
    )
    db_hash_before = file_sha256(Path(manifest["db"]))
    proc = run_child(
        root, "process_application.py", [*process_args(manifest), *extra_args]
    )
    db_changed = file_sha256(Path(manifest["db"])) != db_hash_before
    if command_failed(proc) or db_changed:
        restore_snapshot(snapshot, restore_db=db_changed)
        if db_changed:
            fail(f"{operation} 意外修改了正式 SQL，已恢复数据库和本 job 结果文件。", 1)
        fail(f"{operation} 未完整成功，已恢复本 job 结果文件；正式 SQL 未变化。", 1)
    processed = processed_item_set(manifest)
    processed.update(processed_items or [])
    exact_reused = {
        int(value) for value in manifest.get("exact_reused_items", [])
    }
    exact_reused.update(
        int(value)
        for value in re.findall(r"\[完全一致·复用\]\s*第(\d+)项", proc.stdout or "")
    )
    update_manifest(
        manifest_path,
        manifest,
        phase="committed",
        last_operation=operation,
        last_backup=str(snapshot["db_backup"]),
        processed_items=sorted(processed),
        exact_reused_items=sorted(exact_reused),
    )
    print(f"[job] {manifest_path}")
    print(f"[独立结果] {result}")
    print_next_step(manifest)
    return proc


def cmd_status(args: argparse.Namespace) -> None:
    root = find_project_root()
    db = db_path(root)
    info = database_meta(db)
    print(f"[工程] {root}")
    print(f"[Python] {project_python(root)}")
    print(f"[数据库] {db}")
    if not info.get("exists"):
        print("  状态: 不存在（处理申请前需要同步权威总表）")
    elif info.get("error"):
        print(f"  状态: 无法读取 — {info['error']}")
    else:
        print(
            f"  行数: {info['rows']}（旧库标记 retired=0: {info['main']} / "
            f"retired=1: {info['retired']}）"
        )
        if info["retired"]:
            print(
                "  [提示] 当前库建立于“只读第一工作表”新规则之前，"
                "不能把 retired=0 的记录全部当作第一工作表正式数据。"
                "经用户确认后重新 sync 会只保留第一工作表 J 列非空的行。"
            )
        print(f"  源 Excel: {info.get('source_excel') or '(meta 未记录)'}")
        print(f"  同步时间: {info.get('synced_at') or '(meta 未记录)'}")
        print(f"  文件修改: {info.get('modified_at')}")
    pointer = active_pointer(root)
    if pointer.is_file():
        pdata = read_json(pointer)
        manifest_path = Path(pdata["manifest"])
        print(f"[活动 job] {manifest_path}")
        if manifest_path.is_file():
            job = read_json(manifest_path)
            print(f"  申请表: {job.get('application')}")
            print(f"  结果: {job.get('result')}")
            print(f"  阶段: {job.get('phase')}")
            if job.get("exact_reused_items"):
                print(f"  精确匹配已复用: {job['exact_reused_items']}")
            if job.get("exact_conflict_items"):
                print(f"  精确匹配多编码冲突: {job['exact_conflict_items']}")
            if job.get("invalidated_by_sync"):
                print(f"  已失效: {job['invalidated_by_sync']}")
    else:
        print("[活动 job] 无")
    confirmation_path = database_confirmation_path(root)
    print("[数据库来源刚性确认]")
    if not confirmation_path.is_file():
        print("  下一份申请: 尚未确认")
    else:
        confirmation = read_json(confirmation_path)
        problem = database_confirmation_problem(confirmation, db, info)
        if problem:
            print(f"  下一份申请: 必须重新确认（{problem}）")
        else:
            decision = confirmation.get("decision")
            label = "沿用现有数据库" if decision == "use_existing" else "已同步用户提供的新总表"
            print(f"  下一份申请: 已确认，待一次 prepare 消费（{label}）")
            print(f"  确认时间: {confirmation.get('confirmed_at')}")
    print(f"  [必须询问] {database_confirmation_guidance()}")


def cmd_prepare(args: argparse.Namespace) -> None:
    root = find_project_root()
    application = Path(args.application)
    if not application.is_absolute():
        application = (root / application).resolve()
    if not application.is_file():
        fail(f"申请表不存在: {application}")
    db = db_path(root)
    info = database_meta(db)
    if not info.get("exists") or not info.get("rows"):
        fail("物料库不存在或为空。请先取得用户确认并运行 sync。")
    confirmation_path, confirmation = require_database_confirmation(root, db, info)
    job_dir = unique_job_dir(root, application)
    job_dir.mkdir(parents=True)
    cache = job_dir / "candidates.json"
    result = job_dir / "申请结果.xlsx"
    manifest_path = job_dir / "job.json"
    manifest = {
        "version": SKILL_VERSION,
        "project_root": str(root),
        "application": str(application),
        "db": str(db),
        "cache": str(cache),
        "result": str(result),
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "phase": "preparing",
        "db_at_prepare": info,
        "database_confirmation": {
            "decision": confirmation.get("decision"),
            "confirmed_at": confirmation.get("confirmed_at"),
            "master_excel": confirmation.get("master_excel"),
            "database": confirmation.get("database"),
        },
    }
    atomic_json(manifest_path, manifest)
    child_args = [
        str(application),
        "--db",
        str(db),
        "--cache",
        str(cache),
        "--result",
        str(result),
        "--precompute",
    ]
    proc = run_child(root, "process_application.py", child_args)
    if proc.returncode != 0 or not cache.is_file():
        update_manifest(manifest_path, manifest, phase="prepare_failed")
        fail(f"候选预计算失败；job 保留用于诊断: {manifest_path}", 1)
    reuse_proc = run_child(
        root,
        "process_application.py",
        [
            str(application),
            "--db",
            str(db),
            "--cache",
            str(cache),
            "--result",
            str(result),
            "--reuse-exact",
        ],
    )
    if command_failed(reuse_proc):
        update_manifest(manifest_path, manifest, phase="exact_reuse_failed")
        fail(f"精确匹配复用失败；job 保留用于诊断: {manifest_path}", 1)
    reused = [
        int(value)
        for value in re.findall(r"\[完全一致·复用\]\s*第(\d+)项", reuse_proc.stdout or "")
    ]
    conflicts = [
        int(value)
        for value in re.findall(r"\[完全一致·冲突\]\s*第(\d+)项", reuse_proc.stdout or "")
    ]
    update_manifest(
        manifest_path,
        manifest,
        phase="prepared",
        processed_items=sorted(set(reused)),
        exact_reused_items=sorted(set(reused)),
        exact_conflict_items=sorted(set(conflicts)),
    )
    consume_database_confirmation(confirmation_path, confirmation, manifest_path)
    atomic_json(active_pointer(root), {"manifest": str(manifest_path)})
    print(f"[活动 job] {manifest_path}")
    print(f"[独立结果] {result}")
    print_next_step(manifest)


def cmd_show(args: argparse.Namespace) -> None:
    root = find_project_root()
    _, manifest = resolve_job(root, args.job)
    item = args.item
    if item in processed_item_set(manifest):
        next_item = next_pending_item(manifest, item)
        if next_item is None:
            print(f"[已处理] 第 {item} 项已直接复用总表已有编码；没有后续待确认项目。")
            return
        print(f"[已处理] 第 {item} 项已直接复用总表已有编码，自动跳到第 {next_item} 项。")
        item = next_item
    proc = run_child(
        root,
        "process_application.py",
        [*process_args(manifest), "--show", str(item)],
    )
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def cmd_commit(args: argparse.Namespace) -> None:
    root = find_project_root()
    manifest_path, manifest = resolve_job(root, args.job)
    items = load_cache(manifest)
    picks = validate_picks(items, args.picks)
    processed = processed_item_set(manifest)
    already = [int(spec.split(":", 1)[0]) for spec in picks
               if int(spec.split(":", 1)[0]) in processed]
    if already:
        fail(f"这些申请项已处理，不应再次提交: {already}")
    execute_job_write(
        root,
        manifest_path,
        manifest,
        ["--commit", *picks],
        "commit",
        [int(spec.split(":", 1)[0]) for spec in picks],
    )


def cmd_top1(args: argparse.Namespace) -> None:
    if not args.confirmed:
        fail("top1 会批量生成预定编码；仅在用户明确授权“剩余全部 Top1”后添加 --confirmed。")
    root = find_project_root()
    manifest_path, manifest = resolve_job(root, args.job)
    items = load_cache(manifest)
    start = args.from_item
    processed = processed_item_set(manifest)
    specs = [
        f"{item['idx']}:1"
        for item in items
        if int(item["idx"]) >= start
        and int(item["idx"]) not in processed
        and item.get("candidates")
        and not item.get("exact_conflicts")
    ]
    if not specs:
        print("[无需提交] 指定范围内没有尚待处理且包含候选的申请项。")
        return
    picks = validate_picks(items, specs)
    execute_job_write(
        root,
        manifest_path,
        manifest,
        ["--commit", *picks],
        "top1",
        [int(spec.split(":", 1)[0]) for spec in picks],
    )


def cmd_auto(args: argparse.Namespace) -> None:
    if not args.confirmed:
        fail("auto 会按阈值批量生成预定编码；仅在用户明确要求后添加 --confirmed。")
    if not (0 < args.threshold <= 1):
        fail("阈值必须在 (0,1]。")
    root = find_project_root()
    manifest_path, manifest = resolve_job(root, args.job)
    proc = execute_job_write(
        root,
        manifest_path,
        manifest,
        ["--auto-threshold", str(args.threshold)],
        f"auto-{args.threshold}",
    )
    auto_items = [
        int(value)
        for value in re.findall(r"\[自动选用\]\s*第(\d+)项", proc.stdout or "")
    ]
    if auto_items:
        processed = processed_item_set(manifest)
        processed.update(auto_items)
        update_manifest(
            manifest_path, manifest, processed_items=sorted(processed)
        )


def invalidate_active_job(root: Path, reason: str) -> None:
    pointer = active_pointer(root)
    if not pointer.is_file():
        return
    pdata = read_json(pointer)
    manifest_path = Path(pdata["manifest"])
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        update_manifest(manifest_path, manifest, invalidated_by_sync=reason)


def cmd_use_existing(args: argparse.Namespace) -> None:
    if not args.confirmed:
        fail(
            "仅在用户明确回复沿用现有数据库后添加 --confirmed；"
            "不得把未回复或含糊答复视为同意。"
        )
    root = find_project_root()
    db = db_path(root)
    path, confirmation = record_database_confirmation(root, db, "use_existing")
    identity = confirmation["database"]
    print("[已确认] 下一份申请沿用现有数据库。")
    print(f"  数据库: {identity['path']}")
    print(f"  行数: {identity['rows']}")
    print(f"  源 Excel: {identity.get('source_excel') or '(meta 未记录)'}")
    print(f"  同步时间: {identity.get('synced_at') or '(meta 未记录)'}")
    print(f"  一次性确认记录: {path}")
    print("[下一步] workflow.py prepare <申请表.xlsx>")


def cmd_sync(args: argparse.Namespace) -> None:
    if not args.confirmed:
        fail("sync 会全量重建数据库；仅在用户明确同意后添加 --confirmed。")
    root = find_project_root()
    excel = Path(args.excel)
    if not excel.is_absolute():
        excel = (root / excel).resolve()
    if not excel.is_file():
        fail(f"权威总表不存在: {excel}")
    db = db_path(root)
    invalidate_database_confirmation(root, "用户改为选择新总表同步，旧数据库选择不再有效")
    backups = root / BACKUPS_REL
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backups / f"material_codes-before-sync-{stamp}.db"
    had_db = db.is_file()
    if had_db:
        backup_database(db, backup)
    proc = run_child(
        root, "sync_db.py", ["--excel", str(excel), "--db", str(db)]
    )
    failed = proc.returncode != 0 or "[错误]" in (proc.stdout or "")
    if failed:
        if backup.is_file():
            shutil.copy2(backup, db)
            print(f"[已恢复] {backup} -> {db}")
        elif not had_db and db.exists():
            db.unlink()
            print(f"[已恢复] 删除同步失败时新建的数据库: {db}")
        fail("同步或同步后验证失败，已恢复同步前数据库。", 1)
    invalidate_active_job(root, dt.datetime.now().isoformat(timespec="seconds"))
    confirmation_path, _ = record_database_confirmation(
        root, db, "synced_new_master", excel
    )
    print(f"[同步备份] {backup if backup.is_file() else '(同步前无数据库)'}")
    print(f"[数据库确认] 已记录本次新总表选择，供下一次 prepare 使用: {confirmation_path}")
    print("[提示] 旧 job 候选已失效；继续处理申请前重新 prepare。")


def cmd_search(args: argparse.Namespace) -> None:
    root = find_project_root()
    child_args = [
        args.query,
        "--db",
        str(db_path(root)),
    ]
    proc = run_child(root, "search_material.py", child_args)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def cmd_export(args: argparse.Namespace) -> None:
    root = find_project_root()
    child_args = ["--db", str(db_path(root))]
    if args.template:
        child_args.extend(["--template", str(Path(args.template).resolve())])
    if args.out:
        child_args.extend(["--out", str(Path(args.out).resolve())])
    proc = run_child(root, "export_excel.py", child_args)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Codex-safe workflow for 清力物料编码"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="只读检查工程、数据库和活动 job")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "use-existing", help="记录用户已明确选择沿用现有数据库（一次 prepare 有效）"
    )
    p.add_argument("--confirmed", action="store_true")
    p.set_defaults(func=cmd_use_existing)

    p = sub.add_parser(
        "prepare", help="消费一次数据库来源确认，为申请表创建隔离 job 并预计算候选"
    )
    p.add_argument("application")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("show", help="展示活动 job 的单项候选")
    p.add_argument("item", type=int)
    p.add_argument("--job")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("commit", help="提交明确选择并写独立结果；格式 N:pick")
    p.add_argument("picks", nargs="+")
    p.add_argument("--job")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("top1", help="把指定项起的候选 1 批量生成预定编码")
    p.add_argument("--from-item", type=int, default=1)
    p.add_argument("--confirmed", action="store_true")
    p.add_argument("--job")
    p.set_defaults(func=cmd_top1)

    p = sub.add_parser("auto", help="按明确阈值自动生成 Top1 预定编码")
    p.add_argument("threshold", type=float)
    p.add_argument("--confirmed", action="store_true")
    p.add_argument("--job")
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("sync", help="备份后从权威 Excel 全量同步数据库")
    p.add_argument("excel")
    p.add_argument("--confirmed", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("search", help="只读检索单个物料的相似候选")
    p.add_argument("query")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("export", help="把数据库导出为 Excel")
    p.add_argument("--template")
    p.add_argument("--out")
    p.set_defaults(func=cmd_export)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except RuntimeError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
