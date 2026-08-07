# 考勤分析

本文件夹是考勤分析的独立工程根目录。实现、测试、依赖锁文件和安装说明都保留在本工程内；修改
考勤逻辑时从这里进入，不需要到仓库其他目录寻找代码。

## Agent 入口

先读取 [`skills/audit-attendance-workbook/SKILL.md`](skills/audit-attendance-workbook/SKILL.md)。
该文件定义输入表结构、执行流程、隐私边界、输出和失败行为。

## 安装形态

考勤分析是可全局安装的 Codex Skill。让 Agent 执行：

```text
使用 $skill-installer 从 TuKJet/functional-side-skills 安装
考勤分析/skills/audit-attendance-workbook。安装后进入 Skill 目录执行 uv sync --locked，
告诉我安装结果，并提醒我它会在下一轮对话中生效。
```

人工安装：

```powershell
$codexRoot = if ($env:CODEX_HOME) {
  $env:CODEX_HOME
} else {
  Join-Path $env:USERPROFILE ".codex"
}

py "$codexRoot\skills\.system\skill-installer\scripts\install-skill-from-github.py" `
  --repo "TuKJet/functional-side-skills" `
  --path "考勤分析/skills/audit-attendance-workbook"

Set-Location "$codexRoot\skills\audit-attendance-workbook"
uv sync --locked
```

安装器不会覆盖已有同名目录。更新前应由用户决定保留、备份还是移除旧版本；不要把 GitHub
令牌写进 README、命令日志或对话正文。

## 独立开发

```powershell
Set-Location ".\考勤分析\skills\audit-attendance-workbook"
uv sync --locked
uv run --locked pytest
uv run --locked ruff format --check .
uv run --locked ruff check .
```

主要位置：

- `src/attendance_audit/`：考勤解析、规则和工作簿输出实现；
- `tests/`：独立测试；
- `references/`：详细业务规则和 Agent 兜底格式；
- `SKILL.md`：Agent 实际使用规范。

业务 Excel、审核工作目录、输出文件、缓存和虚拟环境不得提交。
