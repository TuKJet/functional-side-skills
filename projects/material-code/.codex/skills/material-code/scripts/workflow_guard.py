# -*- coding: utf-8 -*-
"""Reject direct execution of internal material-code business scripts."""

from __future__ import annotations

import os
import sys


INTERNAL_CALL_ENV = "MATERIAL_CODE_INTERNAL_WORKFLOW_CALL"
INTERNAL_CALL_VALUE = "workflow-v2"


def require_workflow_entry() -> None:
    if os.environ.get(INTERNAL_CALL_ENV) == INTERNAL_CALL_VALUE:
        return
    print(
        "[错误] 该文件是 material-code 的内部业务脚本，禁止直接执行。"
        "请通过当前 material-code skill 目录下的 scripts/workflow.py 调用，"
        "以确保数据库确认、备份、候选授权和 SQL 不变式得到强制校验。",
        file=sys.stderr,
    )
    raise SystemExit(2)
