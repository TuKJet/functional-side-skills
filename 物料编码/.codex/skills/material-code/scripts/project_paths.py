# -*- coding: utf-8 -*-
"""Portable project discovery shared by the material-code skill scripts."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ENV = "MATERIAL_CODE_ROOT"
PROJECT_NAME = "wuliao-bianma"


def _is_project_root(path: Path) -> bool:
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return f'name = "{PROJECT_NAME}"' in text or f"name = '{PROJECT_NAME}'" in text


def project_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_root = os.environ.get(PROJECT_ENV)
    if env_root:
        candidates.append(Path(env_root))

    cwd = Path.cwd().resolve()
    candidates.extend([cwd, *cwd.parents])

    skill_path = Path(__file__).resolve()
    candidates.extend(skill_path.parents)
    return candidates


def find_project_root() -> Path:
    seen: set[str] = set()
    for candidate in project_candidates():
        try:
            root = candidate.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(root))
        if key in seen:
            continue
        seen.add(key)
        if _is_project_root(root):
            return root
    raise RuntimeError(
        "找不到物料编码工程根目录。请在含 pyproject.toml 的工程内运行，"
        f"或设置 {PROJECT_ENV} 指向该工程。"
    )


def default_db_path() -> Path:
    return find_project_root() / "material_codes.db"
