"""Conservative folder synchronization for two HR Store working copies.

This module deliberately merges immutable catalog references. It never mirrors
deletions and never overwrites a changed record or source attachment.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .store import KINDS, Store, digest, encode


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_path(store: Store) -> Path:
    return store.safe(".sync/state.json")


def _load_state(store: Store):
    path = _state_path(store)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"同步状态损坏: {exc}") from None
    if not isinstance(value, dict) or not isinstance(value.get("base_catalog_refs"), dict):
        raise ValueError("同步状态格式不兼容")
    return value


def sync_status(local: Store) -> dict:
    """Return local copy and pending-sync status without touching remote data."""
    state = _load_state(local)
    try:
        catalog = local.catalog()
        revision = catalog["revision"]
        counts = {kind: len(catalog["records"][kind]) for kind in KINDS}
    except ValueError as exc:
        return {"configured": bool(state), "synced": False, "error": str(exc)}
    current_refs = _refs(catalog)
    return {"configured": bool(state), "synced": bool(state and current_refs == state.get("base_catalog_refs")), "last_synced_at": state.get("updated_at") if state else None,
            "remote_root": state.get("remote_root") if state else "", "revision": revision,
            "base_revision": state.get("base_revision") if state else None, "counts": counts}


def _refs(catalog):
    return {kind: dict(catalog.get("records", {}).get(kind, {})) for kind in KINDS}


def _ref_equal(left, right):
    return left == right


def _merge_refs(base, local, remote):
    merged, conflicts = {}, []
    for kind in KINDS:
        merged[kind] = {}
        ids = set(base.get(kind, {})) | set(local.get(kind, {})) | set(remote.get(kind, {}))
        for ident in sorted(ids):
            b, l, r = base.get(kind, {}).get(ident), local.get(kind, {}).get(ident), remote.get(kind, {}).get(ident)
            if b is not None and (l is None or r is None):
                raise ValueError(f"同步基线记录在一边缺失，疑似目录损坏：{kind}/{ident}")
            if _ref_equal(l, r):
                choice = l
            elif _ref_equal(l, b):
                choice = r
            elif _ref_equal(r, b):
                choice = l
            else:
                conflicts.append({"kind": kind, "id": ident, "base": b, "local": l, "remote": r})
                continue
            if choice is not None:
                merged[kind][ident] = copy.deepcopy(choice)
    return merged, conflicts


def _ensure_copy(source_store: Store, target_store: Store, relative: str, expected_sha: str):
    source = source_store.safe(relative)
    target = target_store.safe(relative)
    if not source.is_file():
        raise ValueError(f"同步源文件丢失: {relative}")
    content = source.read_bytes()
    if digest(content) != expected_sha:
        raise ValueError(f"同步源文件校验失败: {relative}")
    if target.exists():
        if not target.is_file() or digest(target.read_bytes()) != expected_sha:
            raise ValueError(f"目标已有不同内容，拒绝覆盖: {relative}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + "." + uuid.uuid4().hex + ".sync.tmp")
    try:
        with tmp.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def _record_files(store: Store, refs, kind, ident):
    ref = refs.get(kind, {}).get(ident)
    if not ref:
        return []
    record_path = store.safe(ref["path"])
    if not record_path.is_file() or digest(record_path.read_bytes()) != ref["sha256"]:
        raise ValueError(f"记录文件校验失败: {ident}")
    files = [(ref["path"], ref["sha256"])]
    row = json.loads(record_path.read_text("utf-8"))
    for field in ("resume", "attachment"):
        item = row.get(field)
        if isinstance(item, dict) and item.get("path") and item.get("sha256"):
            files.append((item["path"], item["sha256"]))
        if field == "resume" and isinstance(item, dict):
            transcript = item.get("transcript")
            if isinstance(transcript, dict) and transcript.get("path") and transcript.get("sha256"):
                files.append((transcript["path"], transcript["sha256"]))
    return files


def _copy_all(source, target, refs):
    for kind in KINDS:
        for ident, ref in refs.get(kind, {}).items():
            if not source.safe(ref["path"]).is_file():
                continue
            for relative, sha in _record_files(source, refs, kind, ident):
                _ensure_copy(source, target, relative, sha)


def _publish(store: Store, merged_refs, revision):
    catalog = dict(schema_version=1, revision=revision, updated_at=_now(), updated_by=store.actor, records=merged_refs)
    for kind in KINDS:
        for ident, ref in merged_refs[kind].items():
            if not store.safe(ref["path"]).is_file() or digest(store.safe(ref["path"]).read_bytes()) != ref["sha256"]:
                raise ValueError(f"合并后记录缺失或校验失败: {ident}")
    history = store.safe(f"history/{revision}-{uuid.uuid4().hex}-sync.json")
    store.atomic(history, encode(catalog))
    store.atomic(store.safe("catalog.json"), encode(catalog))
    return catalog


def _conflict_details(conflicts, local, remote):
    details = []
    for item in conflicts:
        ref = item.get("local") or item.get("remote") or {}
        label = f"{item['kind']}/{item['id']}"
        def summary(store, current):
            if not current:
                return None
            try:
                row = json.loads(store.safe(current["path"]).read_text("utf-8"))
                fields = {k: row[k] for k in ("id", "version", "updated_by", "updated_at", "name", "title", "department", "month", "status", "owner", "notes", "score", "coverage", "required_gaps") if k in row}
                fields.update(path=current.get("path"), sha256=current.get("sha256"))
                return fields
            except (OSError, ValueError, json.JSONDecodeError):
                return {"id": item["id"], "version": current.get("version"), "path": current.get("path"), "sha256": current.get("sha256")}
        local_summary = summary(local, item.get("local"))
        remote_summary = summary(remote, item.get("remote"))
        if local_summary:
            label = local_summary.get("name") or local_summary.get("title") or local_summary.get("application_id") or label
        elif remote_summary:
            label = remote_summary.get("name") or remote_summary.get("title") or remote_summary.get("application_id") or label
        details.append(dict(item, business_label=label, local_summary=local_summary, remote_summary=remote_summary))
    return details


def synchronize(local: Store, remote_root, initialize_remote=False, resolutions=None) -> dict:
    """Synchronize two copies using a three-way merge of immutable refs."""
    remote = Store(remote_root, local.actor)
    if remote.root == local.root:
        raise ValueError("本地工作副本和远端不能是同一目录")
    if local.root.is_relative_to(remote.root) or remote.root.is_relative_to(local.root):
        raise ValueError("本地和远端目录不能互相包含")
    if not remote.root.exists():
        if not initialize_remote:
            raise ValueError('远端不可用或尚未初始化；本地修改已保留')
        remote.root.mkdir(parents=True, exist_ok=True)
    with local.lock():
        with remote.lock():
            # Read and merge only after both locks: another client may have
            # published while this call was waiting for the lock.
            local_catalog = local.catalog()
            local_state = _load_state(local)
            if local_state and Path(local_state.get('remote_root', '')).resolve() != remote.root:
                raise ValueError('本地同步配置指向了另一远端，拒绝自动切换')
            if not remote.safe("catalog.json").is_file():
                if not initialize_remote:
                    raise ValueError("远端尚未初始化；空远端请显式选择 initialize_remote")
                remote.root.mkdir(parents=True, exist_ok=True)
                existing = [p for p in remote.root.iterdir() if p.name != ".write.lock"]
                if existing:
                    raise ValueError("远端目录非空但没有 catalog.json，拒绝猜测其用途")
                remote.atomic(remote.safe("catalog.json"), encode({"schema_version": 1, "revision": 0, "updated_at": _now(), "updated_by": local.actor, "records": {k: {} for k in KINDS}}))
            remote_catalog = remote.catalog()
            if _state_path(remote).is_file():
                raise ValueError("远端是共享 Hub，不写入或绑定远端同步状态")
            local_refs, remote_refs = _refs(local_catalog), _refs(remote_catalog)
            base = (local_state or {}).get("base_catalog_refs")
            local_empty = not any(local_refs[k] for k in KINDS)
            remote_empty = not any(remote_refs[k] for k in KINDS)
            if remote_empty and not local_empty and not initialize_remote:
                raise ValueError("远端已有空目录；发布本地业务必须显式 initialize_remote")
            if base is None:
                if local_refs == remote_refs:
                    # A previous run may have published one side before its
                    # state file was written; identical catalogs are safe to
                    # adopt as the new base.
                    base = copy.deepcopy(local_refs)
                elif local_empty or remote_empty:
                    base = {k: {} for k in KINDS}
                else:
                    raise ValueError("双方都有业务且没有同步基线，不能直接合并；请选择空本地下载或空远端初始化")
            merged, conflicts = _merge_refs(base, local_refs, remote_refs)
            if resolutions is not None and not conflicts:
                raise ValueError('当前没有待解决冲突，请刷新同步状态')
            if conflicts:
                details = _conflict_details(conflicts, local, remote)
                if resolutions is None:
                    return {"synced": False, "conflicts": details, "revision": local_catalog["revision"], "remote_revision": remote_catalog["revision"]}
                if not isinstance(resolutions, list):
                    raise ValueError("resolutions 必须是列表")
                expected_keys = {(x["kind"], x["id"]) for x in conflicts}
                supplied = set()
                for decision in resolutions:
                    if not isinstance(decision, dict) or (decision.get("kind"), decision.get("id")) in supplied or (decision.get("kind"), decision.get("id")) not in expected_keys:
                        raise ValueError("resolutions 包含缺失、重复或多余的冲突项")
                    supplied.add((decision.get("kind"), decision.get("id")))
                    conflict = next(x for x in conflicts if (x["kind"], x["id"]) == (decision["kind"], decision["id"]))
                    take = decision.get("take")
                    selected = conflict.get("local") if take == "local" else conflict.get("remote") if take == "remote" else None
                    if (not selected or decision.get('local_sha256') != conflict['local']['sha256'] or
                            decision.get('remote_sha256') != conflict['remote']['sha256']):
                        raise ValueError("冲突决策 SHA 不匹配当前版本，拒绝覆盖")
                if supplied != expected_keys:
                    raise ValueError("必须为所有当前冲突提供决策")
                for decision in resolutions:
                    conflict = next(x for x in conflicts if (x["kind"], x["id"]) == (decision["kind"], decision["id"]))
                    selected = conflict[decision["take"]]
                    source = local if decision["take"] == "local" else remote
                    source_refs = local_refs if decision['take'] == 'local' else remote_refs
                    for relative, sha in _record_files(source, source_refs, decision['kind'], decision['id']):
                        for target in (local, remote):
                            _ensure_copy(source, target, relative, sha)
                    row = json.loads(source.safe(selected["path"]).read_text("utf-8"))
                    row["version"] = max(conflict.get("local", {}).get("version", 0), conflict.get("remote", {}).get("version", 0)) + 1
                    row["updated_at"], row["updated_by"] = _now(), local.actor
                    relative = f"records/{decision['kind']}/{decision['id']}/{row['version']}-{uuid.uuid4().hex}.json"
                    content = encode(row)
                    local.atomic(local.safe(relative), content)
                    remote.atomic(remote.safe(relative), content)
                    merged[decision["kind"]][decision["id"]] = {"path": relative, "sha256": digest(content), "version": row["version"]}
            _copy_all(local, remote, merged)
            _copy_all(remote, local, merged)
            for kind in KINDS:
                for ident, ref in merged[kind].items():
                    for relative, sha in _record_files(local if local.safe(ref["path"]).is_file() else remote, merged, kind, ident):
                        for target in (local, remote):
                            if not target.safe(relative).is_file() or digest(target.safe(relative).read_bytes()) != sha:
                                raise ValueError(f"合并记录原件缺失或校验失败: {ident}/{relative}")
            remote_new = _publish(remote, merged, remote_catalog["revision"] + 1)
            local_new = _publish(local, merged, local_catalog["revision"] + 1)
            state = {"version": 1, "remote_root": str(remote.root), "base_revision": local_new["revision"], "base_catalog_refs": merged, "updated_at": _now()}
            local.atomic(_state_path(local), encode(state))
    return {"synced": True, "revision": local_new["revision"], "remote_revision": remote_new["revision"], "conflicts": [], "counts": {k: len(merged[k]) for k in KINDS}}
