#!/usr/bin/env python3
"""Generate a Markdown-first MES process report and an approval-gated HTML report."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from openpyxl import load_workbook
from PIL import Image, ImageOps, UnidentifiedImageError


REQUIRED_HEADERS = ("工步描述", "设备参数详情", "文件绝对路径")
INTERNAL_ONLY_HEADERS = frozenset({"产品规格"})
CONTEXT_HEADERS = (
    "批次编号",
    "工单号",
    "项目名称",
    "工步",
    "设备描述",
    "事件时间",
    "配方",
    "节点",
    "设备编号",
    "加工片数（pcs）",
)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
ARCHIVE_EXTENSIONS = {".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".rar", ".7z"}
DEFAULT_MAX_SOURCE_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_IMAGE_BYTES = 60 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 120_000_000


class ReportError(RuntimeError):
    pass


@dataclass
class Record:
    sequence: int
    excel_row: int
    title: str
    parameters: list[tuple[str, str]]
    metadata: list[tuple[str, str]]
    references: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    name: str
    identity: str
    loader: Callable[[], bytes]

    @property
    def display_priority(self) -> bool:
        return basename(self.name).casefold().startswith("display")


@dataclass
class RenderedImage:
    caption: str
    width: int
    height: int
    full_bytes: bytes
    thumb_bytes: bytes


@dataclass
class RunSummary:
    records: int = 0
    references: int = 0
    candidates: int = 0
    rendered_images: int = 0
    warnings: int = 0


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def natural_key(value: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value))


def basename(value: str) -> str:
    clean = value.split("?", 1)[0].replace("\\", "/").rstrip("/")
    return urllib.parse.unquote(clean.rsplit("/", 1)[-1]) or "attachment"


def suffix_of(value: str) -> str:
    return Path(basename(value)).suffix.casefold()


def is_image_name(value: str) -> bool:
    return suffix_of(value) in IMAGE_EXTENSIONS


def archive_kind(value: str) -> str | None:
    lower = basename(value).casefold()
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return "tar"
    if lower.endswith(".tar.bz2") or lower.endswith(".tar.xz") or lower.endswith(".tar"):
        return "tar"
    suffix = Path(lower).suffix
    return {".zip": "zip", ".rar": "rar", ".7z": "7z"}.get(suffix)


def split_references(value: Any) -> list[str]:
    if value is None:
        return []
    return [part.strip() for part in re.split(r"[\r\n;；]+", str(value)) if part.strip()]


def flatten_parameters(value: Any) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []

    def walk(prefix: str, item: Any) -> None:
        if isinstance(item, dict):
            if not item and prefix:
                rows.append((prefix, "—"))
            for key, child in item.items():
                walk(f"{prefix}.{key}" if prefix else str(key), child)
        elif isinstance(item, list):
            if all(not isinstance(child, (dict, list)) for child in item):
                rows.append((prefix or "值", "、".join(text_value(child) for child in item)))
            else:
                for index, child in enumerate(item, 1):
                    walk(f"{prefix}[{index}]" if prefix else f"[{index}]", child)
        else:
            rows.append((prefix or "值", text_value(item) or "—"))

    walk("", value)
    return rows


def parse_parameters(raw: Any) -> tuple[list[tuple[str, str]], str | None]:
    if raw is None or not str(raw).strip():
        return [], None
    source = str(raw).strip()
    try:
        return flatten_parameters(json.loads(source)), None
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    try:
        parsed = ast.literal_eval(source)
        if isinstance(parsed, (dict, list, tuple)):
            return flatten_parameters(parsed), "设备参数不是标准 JSON，已按兼容格式解析。"
    except (SyntaxError, ValueError):
        pass
    pairs: list[tuple[str, str]] = []
    for part in re.split(r"[\r\n,，;；]+", source):
        match = re.match(r"\s*([^:=：]+?)\s*[:=：]\s*(.*?)\s*$", part)
        if match:
            pairs.append((match.group(1).strip(), match.group(2).strip() or "—"))
    if pairs:
        return pairs, "设备参数已按简易键值文本解析。"
    return [("原始设备参数", source)], "设备参数无法结构化，已保留原文。"


def workbook_records(path: Path, sheet_name: str | None, header_row: int, max_rows: int | None) -> tuple[str, list[Record]]:
    if path.suffix.casefold() != ".xlsx":
        raise ReportError("输入文件必须是 .xlsx 格式。")
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ReportError(f"无法读取工作簿：{type(exc).__name__}") from exc
    try:
        if sheet_name and sheet_name not in workbook.sheetnames:
            raise ReportError(f"找不到工作表：{sheet_name}")
        sheet = workbook[sheet_name] if sheet_name else workbook.active
        header_values = next(sheet.iter_rows(min_row=header_row, max_row=header_row, values_only=True), ())
        headers: dict[str, int] = {}
        for index, value in enumerate(header_values):
            label = text_value(value)
            # Privacy boundary: discard internal-only columns before building the
            # column map so their row values are never accessed or parsed.
            if label in INTERNAL_ONLY_HEADERS:
                continue
            if label and label not in headers:
                headers[label] = index
        missing = [name for name in REQUIRED_HEADERS if name not in headers]
        if missing:
            raise ReportError("缺少必需表头：" + "、".join(missing))

        records: list[Record] = []
        for excel_row, values in enumerate(sheet.iter_rows(min_row=header_row + 1, values_only=True), header_row + 1):
            param_raw = values[headers["设备参数详情"]] if headers["设备参数详情"] < len(values) else None
            ref_raw = values[headers["文件绝对路径"]] if headers["文件绝对路径"] < len(values) else None
            desc_raw = values[headers["工步描述"]] if headers["工步描述"] < len(values) else None
            if param_raw in (None, "") and ref_raw in (None, "") and desc_raw in (None, ""):
                continue
            parameters, warning = parse_parameters(param_raw)
            metadata: list[tuple[str, str]] = []
            for name in CONTEXT_HEADERS:
                index = headers.get(name)
                if index is not None and index < len(values):
                    display = text_value(values[index])
                    if display:
                        metadata.append((name, display))
            record = Record(
                sequence=len(records) + 1,
                excel_row=excel_row,
                title=text_value(desc_raw) or f"工艺段 {len(records) + 1:02d}",
                parameters=parameters,
                metadata=metadata,
                references=split_references(ref_raw),
            )
            if warning:
                record.warnings.append(warning)
            records.append(record)
            if max_rows and len(records) >= max_rows:
                break
        return sheet.title, records
    finally:
        workbook.close()


def safe_member(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in path.parts[0]:
        return None
    return str(path)


class Downloader:
    def __init__(self, root: Path, timeout: int, max_source_bytes: int, max_total_bytes: int, no_download: bool):
        self.root = root
        self.timeout = timeout
        self.max_source_bytes = max_source_bytes
        self.max_total_bytes = max_total_bytes
        self.no_download = no_download
        self.total_bytes = 0
        self.cache: dict[str, Path] = {}
        (root / "downloads").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def request_url(raw: str) -> str:
        # MES exports may contain literal spaces, Chinese characters, and # inside filenames.
        return urllib.parse.quote(raw, safe=":/?&=%@+,;[]")

    def fetch(self, source: str) -> Path:
        if source in self.cache:
            return self.cache[source]
        if self.no_download:
            raise ReportError("网络下载已禁用")
        request = urllib.request.Request(
            self.request_url(source),
            headers={"User-Agent": "MES-Process-Report/1.0"},
        )
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
        target = self.root / "downloads" / f"{digest}{suffix_of(source)}"
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response, target.open("wb") as stream:
                announced = response.headers.get("Content-Length")
                if announced and int(announced) > self.max_source_bytes:
                    raise ReportError("附件超过单文件下载上限")
                size = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_source_bytes:
                        raise ReportError("附件超过单文件下载上限")
                    if self.total_bytes + size > self.max_total_bytes:
                        raise ReportError("附件超过本次运行总下载上限")
                    stream.write(chunk)
            self.total_bytes += size
        except urllib.error.HTTPError as exc:
            target.unlink(missing_ok=True)
            raise ReportError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            target.unlink(missing_ok=True)
            raise ReportError(f"网络错误：{type(exc.reason).__name__}") from exc
        except Exception:
            target.unlink(missing_ok=True)
            raise
        self.cache[source] = target
        return target


def read_file(path: Path, max_bytes: int) -> bytes:
    size = path.stat().st_size
    if size > max_bytes:
        raise ReportError("图片文件超过解码上限")
    return path.read_bytes()


def zip_candidates(path: Path, label: str, max_image_bytes: int) -> tuple[list[Candidate], list[str]]:
    candidates: list[Candidate] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            infos = sorted((info for info in archive.infolist() if not info.is_dir()), key=lambda info: natural_key(info.filename))
            for info in infos:
                member = safe_member(info.filename)
                if not member or not is_image_name(member) or info.file_size > max_image_bytes:
                    continue

                def loader(archive_path: Path = path, member_name: str = info.filename) -> bytes:
                    with zipfile.ZipFile(archive_path) as current:
                        return current.read(member_name)

                candidates.append(Candidate(member, f"{label}!{member}", loader))
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        warnings.append(f"压缩包 {basename(label)} 无法读取：{type(exc).__name__}")
    return candidates, warnings


def tar_candidates(path: Path, label: str, max_image_bytes: int) -> tuple[list[Candidate], list[str]]:
    candidates: list[Candidate] = []
    warnings: list[str] = []
    try:
        with tarfile.open(path, "r:*") as archive:
            infos = sorted((info for info in archive.getmembers() if info.isfile()), key=lambda info: natural_key(info.name))
            for info in infos:
                member = safe_member(info.name)
                if not member or not is_image_name(member) or info.size > max_image_bytes:
                    continue

                def loader(archive_path: Path = path, member_name: str = info.name) -> bytes:
                    with tarfile.open(archive_path, "r:*") as current:
                        extracted = current.extractfile(member_name)
                        if extracted is None:
                            raise ReportError("无法读取 TAR 成员")
                        return extracted.read()

                candidates.append(Candidate(member, f"{label}!{member}", loader))
    except (tarfile.TarError, OSError) as exc:
        warnings.append(f"压缩包 {basename(label)} 无法读取：{type(exc).__name__}")
    return candidates, warnings


def system_tar_candidates(path: Path, label: str, temp_root: Path, max_image_bytes: int) -> tuple[list[Candidate], list[str]]:
    executable = shutil.which("tar")
    if not executable:
        return [], [f"压缩包 {basename(label)} 缺少可用的 RAR 解码器。"]
    folder = temp_root / "rar" / hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:20]
    try:
        listing = subprocess.run(
            [executable, "-tf", str(path)],
            capture_output=True,
            check=False,
            timeout=120,
        )
        if listing.returncode != 0:
            raise ReportError("系统 tar 无法列出 RAR 内容")
        listed_names = listing.stdout.decode("utf-8", errors="replace").splitlines()
        if any(name.strip() and safe_member(name.strip()) is None for name in listed_names):
            raise ReportError("RAR 包含不安全的成员路径")
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)
            extracted = subprocess.run(
                [executable, "-xf", str(path), "-C", str(folder)],
                capture_output=True,
                check=False,
                timeout=300,
            )
            if extracted.returncode != 0:
                raise ReportError("系统 tar 无法解压 RAR")
        candidates: list[Candidate] = []
        for child in sorted((item for item in folder.rglob("*") if item.is_file()), key=lambda item: natural_key(str(item.relative_to(folder)))):
            if not is_image_name(child.name) or child.stat().st_size > max_image_bytes:
                continue
            relative = str(child.relative_to(folder)).replace("\\", "/")
            candidates.append(Candidate(relative, f"{label}!{relative}", lambda item=child: read_file(item, max_image_bytes)))
        return candidates, []
    except Exception as exc:
        return [], [f"压缩包 {basename(label)} 无法读取：{type(exc).__name__}"]


def rar_candidates(path: Path, label: str, temp_root: Path, max_image_bytes: int) -> tuple[list[Candidate], list[str]]:
    system_candidates, system_warnings = system_tar_candidates(path, label, temp_root, max_image_bytes)
    if system_candidates:
        return system_candidates, []
    try:
        import rarfile  # type: ignore
    except ImportError:
        return [], system_warnings or [f"压缩包 {basename(label)} 需要可选依赖 rarfile 和可用的 RAR 解码器。"]
    candidates: list[Candidate] = []
    try:
        with rarfile.RarFile(path) as archive:
            infos = sorted((info for info in archive.infolist() if not info.isdir()), key=lambda info: natural_key(info.filename))
            for info in infos:
                member = safe_member(info.filename)
                if not member or not is_image_name(member) or info.file_size > max_image_bytes:
                    continue

                def loader(archive_path: Path = path, member_name: str = info.filename) -> bytes:
                    with rarfile.RarFile(archive_path) as current:
                        return current.read(member_name)

                candidates.append(Candidate(member, f"{label}!{member}", loader))
        return candidates, []
    except Exception as exc:
        return [], system_warnings + [f"压缩包 {basename(label)} 无法读取：{type(exc).__name__}"]


def seven_zip_candidates(path: Path, label: str, temp_root: Path, max_image_bytes: int) -> tuple[list[Candidate], list[str]]:
    try:
        import py7zr  # type: ignore
    except ImportError:
        return [], [f"压缩包 {basename(label)} 需要可选依赖 py7zr。"]
    candidates: list[Candidate] = []
    try:
        with py7zr.SevenZipFile(path, mode="r") as archive:
            infos = sorted(archive.list(), key=lambda info: natural_key(info.filename))
            for info in infos:
                member = safe_member(info.filename)
                size = int(getattr(info, "uncompressed", 0) or 0)
                if not member or getattr(info, "is_directory", False) or not is_image_name(member) or size > max_image_bytes:
                    continue

                def loader(archive_path: Path = path, member_name: str = info.filename) -> bytes:
                    folder = temp_root / "7z" / hashlib.sha256(f"{archive_path}!{member_name}".encode()).hexdigest()[:20]
                    folder.mkdir(parents=True, exist_ok=True)
                    target = (folder / Path(member_name.replace("/", os.sep))).resolve()
                    if folder.resolve() not in target.parents:
                        raise ReportError("拒绝不安全的 7z 成员路径")
                    if not target.exists():
                        with py7zr.SevenZipFile(archive_path, mode="r") as current:
                            current.extract(path=folder, targets=[member_name])
                    return read_file(target, max_image_bytes)

                candidates.append(Candidate(member, f"{label}!{member}", loader))
        return candidates, []
    except Exception as exc:
        return [], [f"压缩包 {basename(label)} 无法读取：{type(exc).__name__}"]


def archive_candidates(path: Path, label: str, kind: str, temp_root: Path, max_image_bytes: int) -> tuple[list[Candidate], list[str]]:
    if kind == "zip":
        return zip_candidates(path, label, max_image_bytes)
    if kind == "tar":
        return tar_candidates(path, label, max_image_bytes)
    if kind == "rar":
        return rar_candidates(path, label, temp_root, max_image_bytes)
    if kind == "7z":
        return seven_zip_candidates(path, label, temp_root, max_image_bytes)
    return [], [f"不支持的压缩包格式：{basename(label)}"]


def local_path_from_reference(reference: str) -> Path:
    if reference.casefold().startswith("file://"):
        value = urllib.parse.unquote(reference[7:])
        if re.match(r"^/[A-Za-z]:/", value):
            value = value[1:]
        return Path(value)
    return Path(reference)


def discover_reference(reference: str, downloader: Downloader, temp_root: Path, max_image_bytes: int) -> tuple[list[Candidate], list[str]]:
    label = basename(reference)
    remote = reference.casefold().startswith(("http://", "https://"))
    if remote:
        kind = archive_kind(reference)
        if kind:
            try:
                archive_path = downloader.fetch(reference)
            except Exception as exc:
                return [], [f"附件 {label} 下载失败：{exc}"]
            return archive_candidates(archive_path, label, kind, temp_root, max_image_bytes)
        if is_image_name(reference):
            return [Candidate(label, reference, lambda ref=reference: read_file(downloader.fetch(ref), max_image_bytes))], []
        try:
            downloaded = downloader.fetch(reference)
        except Exception as exc:
            return [], [f"附件 {label} 下载失败：{exc}"]
        if zipfile.is_zipfile(downloaded):
            return zip_candidates(downloaded, label, max_image_bytes)
        if tarfile.is_tarfile(downloaded):
            return tar_candidates(downloaded, label, max_image_bytes)
        return [Candidate(label, reference, lambda path=downloaded: read_file(path, max_image_bytes))], []

    path = local_path_from_reference(reference)
    if not path.exists():
        return [], [f"本地附件不存在：{label}"]
    if path.is_dir():
        candidates: list[Candidate] = []
        warnings: list[str] = []
        for child in sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: natural_key(str(item.relative_to(path)))):
            if is_image_name(child.name):
                relative = str(child.relative_to(path)).replace("\\", "/")
                candidates.append(Candidate(relative, str(child.resolve()), lambda item=child: read_file(item, max_image_bytes)))
            else:
                kind = archive_kind(child.name)
                if kind:
                    found, found_warnings = archive_candidates(child, child.name, kind, temp_root, max_image_bytes)
                    candidates.extend(found)
                    warnings.extend(found_warnings)
        return candidates, warnings
    kind = archive_kind(path.name)
    if kind:
        return archive_candidates(path, path.name, kind, temp_root, max_image_bytes)
    if is_image_name(path.name):
        return [Candidate(path.name, str(path.resolve()), lambda item=path: read_file(item, max_image_bytes))], []
    return [], [f"无法识别附件类型：{label}"]


def deduplicate(candidates: Iterable[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    result: list[Candidate] = []
    for candidate in candidates:
        if candidate.identity in seen:
            continue
        seen.add(candidate.identity)
        result.append(candidate)
    return result


def evenly_spaced(items: list[Candidate], count: int) -> list[Candidate]:
    if count <= 0 or not items:
        return []
    if count >= len(items):
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    indices = [round(index * (len(items) - 1) / (count - 1)) for index in range(count)]
    chosen: list[Candidate] = []
    used: set[int] = set()
    for index in indices:
        if index not in used:
            used.add(index)
            chosen.append(items[index])
    for index, item in enumerate(items):
        if len(chosen) >= count:
            break
        if index not in used:
            chosen.append(item)
    return chosen


def select_candidates(candidates: list[Candidate], limit: int) -> tuple[list[Candidate], str | None]:
    priority = [candidate for candidate in candidates if candidate.display_priority]
    if len(priority) >= limit:
        warning = None
        if len(priority) > limit:
            warning = f"发现 {len(priority)} 张 display 前缀图片，受每段 {limit} 张上限约束，仅展示前 {limit} 张。"
        return priority[:limit], warning
    priority_ids = {candidate.identity for candidate in priority}
    remainder = [candidate for candidate in candidates if candidate.identity not in priority_ids]
    chosen = priority + evenly_spaced(remainder, limit - len(priority))
    order = {candidate.identity: index for index, candidate in enumerate(candidates)}
    return sorted(chosen, key=lambda item: order[item.identity]), None


def render_image(candidate: Candidate, max_image_bytes: int) -> RenderedImage:
    raw = candidate.loader()
    if len(raw) > max_image_bytes:
        raise ReportError("图片文件超过解码上限")
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            opened.seek(0)
            image = ImageOps.exif_transpose(opened).convert("RGB")
            full = image.copy()
            full.thumbnail((2000, 1400), Image.Resampling.LANCZOS)
            thumb = image.copy()
            thumb.thumbnail((560, 380), Image.Resampling.LANCZOS)
            full_buffer = io.BytesIO()
            thumb_buffer = io.BytesIO()
            full.save(full_buffer, format="JPEG", quality=88, optimize=True, progressive=True)
            thumb.save(thumb_buffer, format="JPEG", quality=78, optimize=True, progressive=True)
            return RenderedImage(
                caption=basename(candidate.name),
                width=full.width,
                height=full.height,
                full_bytes=full_buffer.getvalue(),
                thumb_bytes=thumb_buffer.getvalue(),
            )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ReportError(f"图片解码失败：{type(exc).__name__}") from exc


def materialize(candidates: list[Candidate], selected: list[Candidate], limit: int, max_image_bytes: int, warnings: list[str]) -> list[RenderedImage]:
    selected_ids = {candidate.identity for candidate in selected}
    queue = selected + [candidate for candidate in candidates if candidate.identity not in selected_ids]
    rendered: list[RenderedImage] = []
    for candidate in queue:
        if len(rendered) >= limit:
            break
        try:
            rendered.append(render_image(candidate, max_image_bytes))
        except Exception as exc:
            warnings.append(f"图片 {basename(candidate.name)} 跳过：{exc}")
    return rendered


def markdown_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def safe_asset_name(record: Record, index: int, caption: str, kind: str) -> str:
    digest = hashlib.sha256(f"{record.excel_row}:{index}:{caption}:{kind}".encode("utf-8")).hexdigest()[:10]
    return f"r{record.excel_row:05d}_{index:02d}_{digest}_{kind}.jpg"


def relative_url(from_file: Path, target: Path) -> str:
    relative = os.path.relpath(target, from_file.parent).replace(os.sep, "/")
    return urllib.parse.quote(relative, safe="/._-")


def metadata_markdown(metadata: list[tuple[str, str]]) -> list[str]:
    if not metadata:
        return []
    parts = [f"**{markdown_escape(key)}：** {markdown_escape(value)}" for key, value in metadata]
    return ["", "　｜　".join(parts), ""]


def render_markdown(title: str, sheet_name: str, records: list[Record], images: dict[int, list[RenderedImage]], output: Path, assets: Path, generated_at: str) -> str:
    total_images = sum(len(value) for value in images.values())
    lines = [
        f"# {markdown_escape(title)}",
        "",
        f"> 来源工作表：{markdown_escape(sheet_name)}　｜　工艺段：{len(records)}　｜　展示图片：{total_images}　｜　生成时间：{generated_at}",
        "",
        "---",
    ]
    for record in records:
        lines.extend(["", f"## {record.sequence:02d} · {markdown_escape(record.title)}"])
        lines.extend(metadata_markdown(record.metadata))
        lines.extend(["", "### 设备参数", "", "| 参数 | 值 |", "| --- | --- |"])
        if record.parameters:
            for key, value in record.parameters:
                lines.append(f"| {markdown_escape(key)} | {markdown_escape(value)} |")
        else:
            lines.append("| — | 未提供设备参数 |")

        # An empty AB cell intentionally produces parameters only, with no image heading or placeholder.
        if record.references:
            lines.extend(["", "### 样品图片", ""])
            current = images.get(record.excel_row, [])
            if current:
                for index, image in enumerate(current, 1):
                    full_path = assets / safe_asset_name(record, index, image.caption, "full")
                    thumb_path = assets / safe_asset_name(record, index, image.caption, "thumb")
                    full_path.write_bytes(image.full_bytes)
                    thumb_path.write_bytes(image.thumb_bytes)
                    lines.extend(
                        [
                            f"[![{markdown_escape(image.caption)}]({relative_url(output, thumb_path)})]({relative_url(output, full_path)})",
                            "",
                            f"*{index:02d} · {markdown_escape(image.caption)} · {image.width}×{image.height}*",
                            "",
                        ]
                    )
            else:
                lines.append("附件存在，但未能提取可展示图片。")
        if record.warnings:
            lines.extend(["", "<details><summary>处理提示</summary>", ""])
            lines.extend(f"- {markdown_escape(warning)}" for warning in record.warnings)
            lines.extend(["", "</details>"])
        lines.extend(["", "---"])
    return "\n".join(lines).rstrip() + "\n"


def html_data_uri(content: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(content).decode("ascii")


def render_html_section(record: Record, rendered: list[RenderedImage]) -> str:
    metadata = "".join(
        f'<div class="meta-item"><span>{html.escape(key)}</span><strong>{html.escape(value)}</strong></div>'
        for key, value in record.metadata
    )
    parameters = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in record.parameters
    ) or "<tr><th>—</th><td>未提供设备参数</td></tr>"
    gallery = ""
    if record.references:
        cards = "".join(
            (
                '<button class="image-card" type="button" '
                f'data-full="{html_data_uri(image.full_bytes)}" data-caption="{html.escape(image.caption, quote=True)}">'
                f'<img loading="lazy" src="{html_data_uri(image.thumb_bytes)}" alt="{html.escape(image.caption, quote=True)}">'
                f'<span><b>{index:02d}</b>{html.escape(image.caption)}</span></button>'
            )
            for index, image in enumerate(rendered, 1)
        )
        gallery = (
            '<div class="gallery-block"><div class="section-label">样品图片</div>'
            + (f'<div class="gallery">{cards}</div>' if cards else '<p class="empty">附件存在，但未能提取可展示图片。</p>')
            + "</div>"
        )
    warnings = ""
    if record.warnings:
        items = "".join(f"<li>{html.escape(value)}</li>" for value in record.warnings)
        warnings = f'<details class="warnings"><summary>处理提示</summary><ul>{items}</ul></details>'
    search = " ".join([record.title] + [value for _, value in record.metadata] + [f"{key} {value}" for key, value in record.parameters])
    return f'''
<article id="process-{record.sequence:03d}" class="process-card" data-search="{html.escape(search.casefold(), quote=True)}">
  <header class="process-head"><span class="sequence">{record.sequence:02d}</span><div><p>PROCESS SEGMENT</p><h2>{html.escape(record.title)}</h2></div></header>
  <div class="meta-grid">{metadata}</div>
  <div class="parameter-block"><div class="section-label">设备参数</div><table><tbody>{parameters}</tbody></table></div>
  {gallery}
  {warnings}
</article>'''


def render_html(title: str, sheet_name: str, records: list[Record], images: dict[int, list[RenderedImage]], template_path: Path, generated_at: str) -> str:
    template = template_path.read_text(encoding="utf-8")
    sections = "\n".join(render_html_section(record, images.get(record.excel_row, [])) for record in records)
    navigation = "\n".join(
        (
            f'<a class="nav-link" href="#process-{record.sequence:03d}" data-process="process-{record.sequence:03d}">'
            f'<span>{record.sequence:02d}</span><b>{html.escape(record.title)}</b></a>'
        )
        for record in records
    )
    replacements = {
        "{{TITLE}}": html.escape(title),
        "{{SHEET}}": html.escape(sheet_name),
        "{{GENERATED_AT}}": html.escape(generated_at),
        "{{PROCESS_COUNT}}": str(len(records)),
        "{{IMAGE_COUNT}}": str(sum(len(value) for value in images.values())),
        "{{NAVIGATION}}": navigation,
        "{{SECTIONS}}": sections,
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


def atomic_write_text(path: Path, content: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ReportError(f"输出文件已存在：{path}；如需覆盖请添加 --overwrite。")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def parse_size_mb(value: float) -> int:
    if value <= 0:
        raise argparse.ArgumentTypeError("大小限制必须大于 0")
    return int(value * 1024 * 1024)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从 MES 导出 Excel 生成 Markdown-first 工艺图文报告。")
    parser.add_argument("input", type=Path, help="MES 导出的 .xlsx 文件")
    parser.add_argument("--output", type=Path, help="输出 .md 或 .html 文件")
    parser.add_argument("--format", choices=("md", "html"), help="默认根据输出扩展名判断；未指定时为 md")
    parser.add_argument("--approved-html", action="store_true", help="确认用户已审阅 Markdown 并明确批准 HTML")
    parser.add_argument("--sheet", help="工作表名称；默认使用活动工作表")
    parser.add_argument("--header-row", type=int, default=1, help="表头行号，默认 1")
    parser.add_argument("--title", default="工艺过程追溯报告", help="报告标题")
    parser.add_argument("--max-images", type=int, default=10, help="每个工艺段最多展示图片数，1-10")
    parser.add_argument("--max-rows", type=int, help="只处理前 N 个非空工艺段，主要用于验证")
    parser.add_argument("--timeout", type=int, default=30, help="单次网络操作超时秒数")
    parser.add_argument("--max-source-mb", type=parse_size_mb, default=DEFAULT_MAX_SOURCE_BYTES, metavar="MB")
    parser.add_argument("--max-total-download-mb", type=parse_size_mb, default=DEFAULT_MAX_TOTAL_BYTES, metavar="MB")
    parser.add_argument("--max-image-mb", type=parse_size_mb, default=DEFAULT_MAX_IMAGE_BYTES, metavar="MB")
    parser.add_argument("--no-download", action="store_true", help="跳过所有 HTTP(S) 下载")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有报告文件或复用已有资源目录")
    return parser


def resolve_format(args: argparse.Namespace) -> tuple[str, Path]:
    if args.output:
        suffix = args.output.suffix.casefold()
        inferred = "html" if suffix in {".html", ".htm"} else "md" if suffix == ".md" else None
    else:
        inferred = None
    format_name = args.format or inferred or "md"
    if args.output:
        output = args.output
    else:
        output = args.input.with_name(args.input.stem + "_工艺报告." + ("html" if format_name == "html" else "md"))
    if format_name == "html" and not args.approved_html:
        raise ReportError("HTML 阶段被门禁阻止：请先返回 Markdown，取得用户明确批准后再添加 --approved-html。")
    if format_name == "md" and output.suffix.casefold() != ".md":
        raise ReportError("Markdown 输出文件必须使用 .md 扩展名。")
    if format_name == "html" and output.suffix.casefold() not in {".html", ".htm"}:
        raise ReportError("HTML 输出文件必须使用 .html 或 .htm 扩展名。")
    return format_name, output.resolve()


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= args.max_images <= 10:
        raise ReportError("--max-images 必须在 1 到 10 之间。")
    if args.header_row < 1:
        raise ReportError("--header-row 必须大于等于 1。")
    input_path = args.input.resolve()
    if not input_path.is_file():
        raise ReportError(f"输入文件不存在：{input_path}")
    format_name, output = resolve_format(args)
    sheet_name, records = workbook_records(input_path, args.sheet, args.header_row, args.max_rows)
    if not records:
        raise ReportError("工作表中没有可生成报告的工艺记录。")

    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    summary = RunSummary(records=len(records), references=sum(len(record.references) for record in records))
    images_by_row: dict[int, list[RenderedImage]] = {}
    with tempfile.TemporaryDirectory(prefix="mes-process-report-") as temporary:
        temp_root = Path(temporary)
        downloader = Downloader(
            temp_root,
            args.timeout,
            args.max_source_mb,
            args.max_total_download_mb,
            args.no_download,
        )
        for record in records:
            # AB empty: parameters only, no media discovery and no image section.
            if not record.references:
                continue
            discovered: list[Candidate] = []
            for reference in record.references:
                found, warnings = discover_reference(reference, downloader, temp_root, args.max_image_mb)
                discovered.extend(found)
                record.warnings.extend(warnings)
            candidates = deduplicate(discovered)
            summary.candidates += len(candidates)
            selected, warning = select_candidates(candidates, args.max_images)
            if warning:
                record.warnings.append(warning)
            images_by_row[record.excel_row] = materialize(
                candidates,
                selected,
                args.max_images,
                args.max_image_mb,
                record.warnings,
            )

        if format_name == "md":
            assets = output.with_name(output.stem + "_assets")
            if assets.exists() and not args.overwrite:
                raise ReportError(f"资源目录已存在：{assets}；如需复用请添加 --overwrite。")
            assets.mkdir(parents=True, exist_ok=True)
            if args.overwrite:
                for previous in assets.glob("r*_*.jpg"):
                    previous.unlink()
            content = render_markdown(args.title, sheet_name, records, images_by_row, output, assets, generated_at)
            atomic_write_text(output, content, args.overwrite)
            assets_output: str | None = str(assets)
        else:
            template = Path(__file__).resolve().parents[1] / "assets" / "report_template.html"
            content = render_html(args.title, sheet_name, records, images_by_row, template, generated_at)
            atomic_write_text(output, content, args.overwrite)
            assets_output = None

    summary.rendered_images = sum(len(value) for value in images_by_row.values())
    summary.warnings = sum(len(record.warnings) for record in records)
    return {
        "format": format_name,
        "output": str(output),
        "assets": assets_output,
        "sheet": sheet_name,
        "records": summary.records,
        "references": summary.references,
        "candidates": summary.candidates,
        "rendered_images": summary.rendered_images,
        "warnings": summary.warnings,
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = run(args)
    except ReportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: 用户中止运行。", file=sys.stderr)
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
