#!/usr/bin/env python3
"""Download and configure a verified HR desktop client from GitHub Releases.

This module never executes a downloaded file.  Shortcut creation is a separate,
explicit command so an Agent can ask the user before invoking it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REPOSITORY = "TuKJet/functional-side-skills"
API_ROOT = "https://api.github.com"
API_HOST = "api.github.com"
ALLOWED_DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
MAX_RELEASE_BYTES = 350 * 1024 * 1024
MAX_CHECKSUM_BYTES = 64 * 1024
VERSION_RE = re.compile(r"^hr-client-v[0-9]+\.[0-9]+\.[0-9]+-run[0-9]+$")


class ClientError(RuntimeError):
    pass


def _platform(value: str | None = None) -> str:
    if value:
        return value
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos-aarch64" if platform.machine().lower() in {"arm64", "aarch64"} else "macos-x86_64"
    raise ClientError("当前系统不支持客户端自动下载；仅支持 Windows 和 macOS。")


def _names(kind: str) -> tuple[str, str]:
    if kind == "windows":
        return "hr-resume-windows-x64.exe", "hr-resume-windows-x64.sha256"
    if kind in {"macos-aarch64", "macos-x86_64"}:
        return f"hr-resume-{kind}.dmg", f"hr-resume-{kind}.sha256"
    raise ClientError(f"不支持的平台标识：{kind}")


def _request(url: str, *, max_bytes: int = MAX_RELEASE_BYTES) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ClientError("客户端下载地址必须使用 HTTPS。")
    if parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS | {API_HOST}:
        raise ClientError(f"拒绝非 GitHub 下载域名：{parsed.hostname}")
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "hr-resume-client/1"})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        raise ClientError(f"GitHub 请求失败：HTTP {exc.code}") from exc
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ClientError("下载文件超过安全大小限制。")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        response.close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
            raise ClientError(f"拒绝重定向到非 GitHub 下载域名：{parsed.hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _releases() -> list[dict]:
    raw = _request(f"{API_ROOT}/repos/{REPOSITORY}/releases?per_page=30", max_bytes=2 * 1024 * 1024)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClientError("GitHub Releases 返回的不是 JSON。") from exc
    if not isinstance(value, list):
        raise ClientError("GitHub Releases 返回格式不正确。")
    return value


def _release(kind: str) -> tuple[dict, dict, dict]:
    asset_name, checksum_name = _names(kind)
    for release in _releases():
        tag = str(release.get("tag_name", ""))
        if release.get("draft") or release.get("prerelease") or not VERSION_RE.fullmatch(tag):
            continue
        assets = {str(asset.get("name")): asset for asset in release.get("assets", []) if isinstance(asset, dict)}
        if asset_name in assets and checksum_name in assets:
            return release, assets[asset_name], assets[checksum_name]
    raise ClientError(f"没有找到可用的 {kind} 客户端 Release。")


def _config_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local")
        return Path(base) / "HRResume" / "client-config.json"
    return Path.home() / "Library/Application Support/HRResume/client-config.json"


def _load_config(skill_root: Path) -> tuple[dict, Path]:
    path = skill_root / "workspace.local.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ClientError("缺少 workspace.local.json，请先完成 Skill 安装。") from exc
    except json.JSONDecodeError as exc:
        raise ClientError("workspace.local.json 不是有效 JSON。") from exc
    required = ("python", "script", "root", "actor", "remote_root", "config_path", "port")
    if not isinstance(value, dict) or any(key not in value for key in required):
        raise ClientError("Skill 配置不完整，需要 python、script、root、actor、remote_root、config_path、port。")
    if any(not isinstance(value[key], str) or not value[key].strip() for key in ("python", "script", "root", "actor", "config_path")):
        raise ClientError("Skill 配置中的 python、script、root、actor、config_path 不能留空。")
    if not isinstance(value["remote_root"], str) or not isinstance(value["port"], int):
        raise ClientError("Skill 配置中的 remote_root 或 port 类型不正确。")
    return value, path


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _checksum(text: bytes, asset_name: str) -> str:
    decoded = text.decode("utf-8", errors="strict")
    for line in decoded.splitlines():
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if match and Path(match.group(2)).name == asset_name:
            return match.group(1).lower()
    raise ClientError(f"校验文件中没有 {asset_name} 的 SHA256。")


def _safe_tag(tag: str) -> str:
    if not VERSION_RE.fullmatch(tag):
        raise ClientError("Release tag 格式不受信任。")
    return tag.replace("/", "_")


def download(skill_root: Path, kind: str) -> dict:
    config, _ = _load_config(skill_root)
    release, asset, checksum_asset = _release(kind)
    name, checksum_name = _names(kind)
    download_url = asset.get("browser_download_url")
    checksum_url = checksum_asset.get("browser_download_url")
    if not isinstance(download_url, str) or not isinstance(checksum_url, str):
        raise ClientError("Release 资源缺少下载地址。")
    payload = _request(download_url)
    checksum = _checksum(_request(checksum_url, max_bytes=MAX_CHECKSUM_BYTES), name)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != checksum:
        raise ClientError(f"SHA256 校验失败：期望 {checksum}，实际 {actual}。")
    tag = _safe_tag(str(release["tag_name"]))
    base = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "HRResume" if sys.platform == "win32"
            else Path.home() / "Library/Application Support/HRResume") / "clients" / tag
    base.mkdir(parents=True, exist_ok=True)
    target = base / name
    if target.exists():
        if target.read_bytes() != payload:
            raise ClientError(f"目标版本已有不同文件，拒绝覆盖：{target}")
        # Preserve a running client and its file identity; no replacement is needed.
    else:
        temporary = target.with_name(target.name + f".{os.getpid()}.download")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    config_path = _config_path()
    updated = dict(config, client_release_tag=tag, client_platform=kind,
                   client_path=str(target), client_sha256=actual,
                   config_path=str(config_path), port=0)
    if config_path.exists():
        backup = config_path.with_name(f"client-config.backup.{time.time_ns()}.json")
        shutil.copy2(config_path, backup)
    _atomic_json(config_path, updated)
    result = {"ok": True, "release": tag, "platform": kind, "sha256": actual,
              "client_path": str(target), "need_install": kind.startswith("macos"),
              "shortcut_available": kind == "windows"}
    if kind == "windows":
        result["executable"] = str(target)
        result["message"] = "客户端已下载。是否需要在桌面创建快捷方式？请用户明确回答后再调用 shortcut。"
    else:
        result["dmg"] = str(target)
        result["message"] = "DMG 已下载，请打开后将招聘看板拖入“应用程序”。"
    return result


def shortcut(executable: Path) -> dict:
    if sys.platform != "win32":
        raise ClientError("macOS 不支持此快捷方式命令，请在 Finder 中将应用拖入程序坞。")
    target = executable.resolve()
    if target.suffix.lower() != ".exe" or not target.is_file():
        raise ClientError("快捷方式目标必须是已下载并存在的 Windows EXE。")
    config_path = _config_path()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClientError("客户端配置不存在或损坏，请先执行 download。") from exc
    if Path(config.get("client_path", "")).resolve() != target or config.get("client_platform") != "windows":
        raise ClientError("快捷方式目标不是最近一次校验通过的客户端。")
    if config.get("client_sha256") != hashlib.sha256(target.read_bytes()).hexdigest():
        raise ClientError("快捷方式目标的 SHA256 与配置不一致。")
    powershell = r"C:\Program Files\PowerShell\7\pwsh.exe" if Path(r"C:\Program Files\PowerShell\7\pwsh.exe").is_file() else "powershell.exe"
    env = dict(os.environ, HR_CLIENT_SHORTCUT_TARGET=str(target))
    folder_script = "[Environment]::GetFolderPath('DesktopDirectory')"
    folder = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", folder_script], env=env,
                            capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if folder.returncode:
        raise ClientError("无法定位 Windows 桌面目录。")
    desktop = Path(folder.stdout.strip()).resolve()
    if not desktop.is_dir():
        raise ClientError("Windows 桌面目录不存在。")
    link = desktop / "招聘工作台.lnk"
    def previous_client_path(path: Path) -> bool:
        clients = _config_path().parent / "clients"
        try:
            return path.name == "hr-resume-windows-x64.exe" and clients in path.parents and VERSION_RE.fullmatch(path.parent.name) is not None
        except OSError:
            return False
    if link.exists():
        existing = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:HR_CLIENT_SHORTCUT_PATH);$s.TargetPath"], env=dict(env, HR_CLIENT_SHORTCUT_PATH=str(link)), capture_output=True, text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        old_target = Path(existing.stdout.strip()).resolve() if not existing.returncode and existing.stdout.strip() else None
        if existing.returncode or (old_target != target and not (old_target and previous_client_path(old_target))):
            raise ClientError(f"桌面已有不同的招聘工作台快捷方式，拒绝覆盖：{link}")
    env = dict(env, HR_CLIENT_SHORTCUT_PATH=str(link))
    script = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:HR_CLIENT_SHORTCUT_PATH);$s.TargetPath=$env:HR_CLIENT_SHORTCUT_TARGET;$s.WorkingDirectory=[IO.Path]::GetDirectoryName($env:HR_CLIENT_SHORTCUT_TARGET);$s.Save()"
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                            capture_output=True, text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ClientError("创建桌面快捷方式失败：" + (result.stderr.strip() or "未知错误"))
    return {"ok": True, "shortcut": str(link), "target": str(target)}


def main(argv=None) -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="HR 招聘客户端安全下载器")
    parser.add_argument("command", choices=["check", "download", "shortcut"])
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--platform", dest="platform_name")
    parser.add_argument("--executable")
    args = parser.parse_args(argv)
    try:
        if args.command == "shortcut":
            result = shortcut(Path(args.executable)) if args.executable else (_ for _ in ()).throw(ClientError("shortcut 必须提供 --executable。"))
        else:
            kind = _platform(args.platform_name)
            release, asset, _checksum_asset = _release(kind)
            if args.command == "check":
                name, checksum_name = _names(kind)
                result = {"ok": True, "release": release["tag_name"], "platform": kind,
                          "asset": name, "checksum": checksum_name,
                          "download_url": asset["browser_download_url"]}
            else:
                result = download(Path(args.skill_root).expanduser().resolve(), kind)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ClientError, OSError, urllib.error.URLError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
