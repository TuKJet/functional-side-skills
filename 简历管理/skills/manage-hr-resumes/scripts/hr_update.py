"""Safe GitHub updater for the installed HR resume Skill.

The updater only owns files shipped by this Skill.  Runtime configuration,
private virtual environments, and HR data are deliberately outside its file
manifest and are never copied, deleted, or replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 50 * 1024 * 1024
CONFIG_NAME = "skills-upstream.json"
UPDATE_DIR = ".update"
MANIFEST = "manifest.json"
MANAGED = ("SKILL.md", "requirements.txt", "agents", "references", "scripts", CONFIG_NAME)
REQUIRED = ("SKILL.md", "requirements.txt", "scripts/hr.py", "scripts/hr_update.py", CONFIG_NAME)


def _managed(name: str) -> bool:
    name = name.replace("\\", "/")
    return not ("__pycache__" in Path(name).parts or name.endswith(".pyc")) and (name in MANAGED or any(name.startswith(prefix + "/") for prefix in MANAGED if prefix not in ("SKILL.md", "requirements.txt", CONFIG_NAME)))


class UpdateError(Exception):
    def __init__(self, code: str, message: str, **data: object):
        super().__init__(message)
        self.code, self.data = code, data


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError("invalid_state", f"无法读取 JSON：{path}", detail=str(exc)) from exc
    if not isinstance(value, dict):
        raise UpdateError("invalid_state", f"JSON 必须是对象：{path}")
    return value


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _requirements_content(path: Path) -> str:
    """Normalize transport-only UTF-8 BOM and newline differences."""
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").decode("utf-8")


def _files(root: Path) -> list[str]:
    result: list[str] = []
    for name in MANAGED:
        path = root / name
        if path.is_file():
            if _managed(name):
                result.append(name)
        elif path.is_dir():
            result.extend(str(p.relative_to(root)).replace("\\", "/") for p in path.rglob("*") if p.is_file() and _managed(str(p.relative_to(root))))
    return sorted(result)


def _manifest(root: Path, commit: str | None = None) -> dict:
    return {"schema": 1, "commit": commit, "files": {name: _digest(root / name) for name in _files(root)}}


def _update_dir(root: Path) -> Path:
    path = root / UPDATE_DIR
    path.mkdir(exist_ok=True)
    return path


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _pending(root: Path) -> Path:
    return _update_dir(root) / "transaction.json"


def _lock(root: Path):
    path = _update_dir(root) / "lock"
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError) as exc:
        handle.close()
        raise UpdateError("busy", "已有更新或回滚正在进行，请稍后重试。", detail=str(exc)) from exc
    return handle


def _unlock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _config(root: Path) -> dict:
    config = _json(root / CONFIG_NAME)
    expected = {"provider": "github", "repository": "TuKJet/functional-side-skills",
                "path": "简历管理/skills/manage-hr-resumes", "branch": "main"}
    if any(config.get(k) != v for k, v in expected.items()):
        raise UpdateError("invalid_upstream", "Skill 上游元数据不匹配固定来源。", expected=expected, actual=config)
    return config


def _validate_manifest(root: Path, state: dict) -> None:
    if state.get("schema") != 1 or not isinstance(state.get("files"), dict):
        raise UpdateError("invalid_state", "manifest 格式无效。")
    missing = [name for name in REQUIRED if name not in state["files"] or not (root / name).is_file()]
    if missing:
        raise UpdateError("invalid_state", "manifest 缺少必要托管文件。", missing=missing)
    for name, digest in state["files"].items():
        if not _managed(name) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise UpdateError("invalid_state", "manifest 含有无效文件或哈希。", file=name)


def _validate_backup(root: Path, state: dict) -> None:
    _validate_manifest(root, state)
    if set(_files(root)) != set(state["files"]):
        raise UpdateError("invalid_backup", "备份托管文件集合与 manifest 不一致。")
    for name, digest in state["files"].items():
        if _digest(root / name) != digest:
            raise UpdateError("invalid_backup", "备份文件哈希校验失败。", file=name)


def _urlopen(url: str):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "hr-skill-updater"})
    return urllib.request.urlopen(request, timeout=20)


def _latest(config: dict) -> dict:
    repo, path, branch = config["repository"], config["path"], config["branch"]
    url = f"https://api.github.com/repos/{repo}/commits?path={urllib.parse.quote(path)}&sha={urllib.parse.quote(branch)}&per_page=1"
    try:
        with _urlopen(url) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("upstream_missing", "GitHub 上游仓库或 Skill 路径不存在。", repository=repo, path=path) from exc
        raise UpdateError("download_failed", f"GitHub 查询失败（HTTP {exc.code}）。") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError("download_failed", "无法查询 GitHub 上游。", detail=str(exc)) from exc
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise UpdateError("upstream_missing", "GitHub 上游尚未提供该 Skill 路径。", repository=repo, path=path)
    commit = payload[0].get("sha")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise UpdateError("download_failed", "GitHub 返回的 commit SHA 无效。")
    return {"repository": repo, "path": path, "branch": branch, "commit": commit.lower()}


def _safe_extract(raw: bytes, root: Path, source: dict) -> Path:
    stage = _update_dir(root) / ("stage-" + uuid.uuid4().hex)
    stage.mkdir()
    total = 0
    prefix = f"functional-side-skills-{source['commit']}/{source['path']}/"
    zip_path = stage / "source.zip"
    zip_path.write_bytes(raw)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = []
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if info.file_size > MAX_FILE or (total := total + info.file_size) > MAX_TOTAL:
                    raise UpdateError("invalid_download", "上游 ZIP 超过安全体积限制。")
                name = info.filename.replace("\\", "/")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise UpdateError("invalid_download", "上游 ZIP 包含符号链接，已拒绝。")
                if name.startswith("/") or ".." in Path(name).parts:
                    raise UpdateError("invalid_download", "上游 ZIP 包含越界路径，已拒绝。")
                if not name.startswith(prefix):
                    continue
                rel = name[len(prefix):]
                if not rel or rel.startswith("/"):
                    continue
                if not _managed(rel):
                    continue
                target = stage / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
                members.append(rel)
    except zipfile.BadZipFile as exc:
        raise UpdateError("invalid_download", "下载内容不是有效 ZIP。") from exc
    finally:
        zip_path.unlink(missing_ok=True)
    if any(required not in members for required in REQUIRED):
        shutil.rmtree(stage, ignore_errors=True)
        raise UpdateError("upstream_missing", "固定 commit 中没有完整的 HR Skill 路径。")
    return stage


def _validate(stage: Path, root: Path) -> None:
    skill = stage / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    if not text.startswith("---") or "name: manage-hr-resumes" not in text:
        raise UpdateError("invalid_download", "新 SKILL.md 缺少有效 frontmatter。")
    for path in stage.rglob("*.py"):
        if not _managed(str(path.relative_to(stage))):
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise UpdateError("invalid_download", f"新 Python 语法校验失败：{path.name}", detail=str(exc)) from exc
    old_req = root / "requirements.txt"
    new_req = stage / "requirements.txt"
    if (not new_req.exists() or not old_req.exists()
            or _requirements_content(old_req) != _requirements_content(new_req)):
        raise UpdateError("dependency_change", "上游 requirements 发生变化，请安装新版本 Skill 后再使用更新。", changed_files=["requirements.txt"])


def _copy_tree(stage: Path, root: Path) -> list[str]:
    old = set(_files(root))
    new = set(_files(stage))
    changed = sorted(
        name for name in old | new
        if ((name in old) != (name in new))
        or ((root / name).exists() and (stage / name).exists() and _digest(root / name) != _digest(stage / name))
    )
    for name in old - new:
        (root / name).unlink()
    for name in new:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name("." + target.name + ".update-" + uuid.uuid4().hex)
        try:
            shutil.copy2(stage / name, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return changed


def _check_local(root: Path, state: dict) -> list[str]:
    expected = state.get("files", {})
    current = _manifest(root).get("files", {})
    return sorted(name for name in set(expected) | set(current) if expected.get(name) != current.get(name))


def command_init(root: Path) -> dict:
    lock = _lock(root)
    try:
        _config(root)
        if _pending(root).exists():
            raise UpdateError("pending_transaction", "存在未完成更新事务，请先执行 rollback。")
        manifest = _manifest(root, None)
        path = _update_dir(root) / MANIFEST
        if path.exists():
            old = _json(path)
            _validate_manifest(root, old)
            local = _check_local(root, old)
            if local:
                raise UpdateError("local_changes", "已有 manifest 基线且托管文件发生变化，未覆盖。", local_changes=local)
            return {"initialized": False, "manifest": str(path), "files": len(old["files"])}
        _atomic_json(path, manifest)
        return {"initialized": True, "manifest": str(path), "files": len(manifest["files"])}
    finally:
        _unlock(lock)


def _command_check_unlocked(root: Path) -> dict:
    if _pending(root).exists():
        raise UpdateError("pending_transaction", "存在未完成更新事务，请先执行 rollback。")
    config = _config(root)
    state_path = _update_dir(root) / MANIFEST
    if not state_path.exists():
        raise UpdateError("manifest_missing", "请先由安装流程调用 init-state 建立初始 manifest。")
    state = _json(state_path)
    local = _check_local(root, state)
    latest = _latest(config)
    return {"current_commit": state.get("commit"), "latest_commit": latest["commit"], "update_available": latest["commit"] != state.get("commit"), "local_changes": local}


def command_check(root: Path) -> dict:
    lock = _lock(root)
    try:
        return _command_check_unlocked(root)
    finally:
        _unlock(lock)


def command_update(root: Path) -> dict:
    lock = _lock(root)
    stage = None
    try:
        config = _config(root)
        manifest_path = _update_dir(root) / MANIFEST
        if _pending(root).exists():
            raise UpdateError("pending_transaction", "存在未完成事务，请先执行 rollback 或恢复。")
        if not manifest_path.exists():
            raise UpdateError("manifest_missing", "请先由安装流程调用 init-state 建立初始 manifest。")
        state = _json(manifest_path)
        _validate_manifest(root, state)
        local = _check_local(root, state)
        if local:
            raise UpdateError("local_changes", "本地托管文件已有修改，未覆盖。", local_changes=local)
        latest = _latest(config)
        if latest["commit"] == state.get("commit"):
            return {"updated": False, "commit": latest["commit"], "changed_files": []}
        archive_url = f"https://github.com/{config['repository']}/archive/{latest['commit']}.zip"
        try:
            with _urlopen(archive_url) as response:
                raw = response.read(MAX_TOTAL + 1)
        except (OSError, urllib.error.HTTPError) as exc:
            raise UpdateError("download_failed", "Skill 更新包下载失败。", detail=str(exc)) from exc
        if len(raw) > MAX_TOTAL:
            raise UpdateError("invalid_download", "下载内容超过安全体积限制。")
        stage = _safe_extract(raw, root, latest)
        _validate(stage, root)
        previous = state.get("commit") or "unknown"
        backup = _update_dir(root) / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + previous[:12])
        backup.mkdir(parents=True)
        for name in _files(root):
            target = backup / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, target)
        _atomic_json(backup / "manifest.json", state)
        _atomic_json(_pending(root), {"schema": 1, "backup": str(backup), "old_manifest": state, "target_commit": latest["commit"]})
        try:
            changed = _copy_tree(stage, root)
            new_state = _manifest(root, latest["commit"])
            _atomic_json(manifest_path, new_state)
            _pending(root).unlink(missing_ok=True)
        except Exception as exc:
            # A filesystem failure after staging must not leave a mixed Skill.
            _copy_tree(backup, root)
            _atomic_json(manifest_path, state)
            _pending(root).unlink(missing_ok=True)
            raise UpdateError("update_failed", "替换 Skill 文件失败，已从备份恢复。", detail=str(exc)) from exc
        package = _refresh_package_unlocked(root)["package"]
        return {"updated": True, "commit": latest["commit"], "changed_files": changed, "backup": str(backup), "package": package}
    except UpdateError:
        raise
    finally:
        if stage:
            shutil.rmtree(stage, ignore_errors=True)
        _unlock(lock)


def command_rollback(root: Path, backup: str | None) -> dict:
    lock = _lock(root)
    try:
        base = _update_dir(root) / "backups"
        pending = _json(_pending(root)) if _pending(root).exists() else None
        candidates = [Path(backup).resolve()] if backup else ([Path(pending["backup"]).resolve()] if pending else sorted((p for p in base.iterdir() if p.is_dir()), reverse=True) if base.exists() else [])
        if not candidates or not candidates[0].is_dir():
            raise UpdateError("backup_missing", "没有可用的 Skill 备份。")
        source = candidates[0]
        if base.resolve() not in source.parents:
            raise UpdateError("invalid_backup", "备份必须位于当前 Skill 的 .update/backups 内。")
        old = _json(source / "manifest.json")
        _validate_backup(source, old)
        current_path = _update_dir(root) / MANIFEST
        current = _json(current_path) if current_path.exists() else None
        if current and not pending:
            _validate_manifest(root, current)
            local = _check_local(root, current)
            if local:
                raise UpdateError("local_changes", "当前托管文件已有修改，未回退。", local_changes=local)
        if any(_digest(source / name) != digest for name, digest in old["files"].items()):
            raise UpdateError("invalid_backup", "备份文件哈希校验失败，未回退。")
        _atomic_json(_pending(root), {"schema": 1, "kind": "rollback", "backup": str(source), "old_manifest": current or {}, "target_commit": old.get("commit")})
        try:
            changed = _copy_tree(source, root)
            _atomic_json(current_path, old)
            _pending(root).unlink(missing_ok=True)
        except (OSError, UpdateError) as exc:
            raise UpdateError("rollback_failed", "回退中断；transaction.json 已保留，请重试 rollback。", detail=str(exc), backup=str(source)) from exc
        return {"rolled_back": True, "commit": old.get("commit"), "changed_files": changed, "backup": str(source), "package": _refresh_package_unlocked(root)["package"]}
    finally:
        _unlock(lock)


def _refresh_package_unlocked(root: Path) -> dict:
    """Create the local WorkBuddy import package without including update state."""
    _config(root)
    package = _update_dir(root) / "workbuddy-package.zip"
    temporary = package.with_name("workbuddy-package-" + uuid.uuid4().hex + ".zip")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in _files(root):
            archive.write(root / name, name)
        workspace = root / "workspace.local.json"
        if workspace.is_file():
            archive.write(workspace, "workspace.local.json")
    os.replace(temporary, package)
    return {"package": str(package), "files": len(_files(root)) + int((root / "workspace.local.json").is_file())}


def command_refresh_package(root: Path) -> dict:
    lock = _lock(root)
    try:
        if _pending(root).exists():
            raise UpdateError("pending_transaction", "存在未完成更新事务，请先执行 rollback。")
        return _refresh_package_unlocked(root)
    finally:
        _unlock(lock)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init-state", "check", "update", "rollback", "refresh-package"])
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--backup")
    args = parser.parse_args(argv)
    try:
        root = Path(args.skill_root).resolve()
        result = command_init(root) if args.command == "init-state" else command_check(root) if args.command == "check" else command_update(root) if args.command == "update" else command_rollback(root, args.backup) if args.command == "rollback" else command_refresh_package(root)
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
        return 0
    except UpdateError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "message": str(exc), **exc.data}, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "internal_error", "message": str(exc)}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
