# Functional Side Skills

职能侧 Agent 能力的统一仓库。这里同时容纳可全局安装的 Codex Skill、必须在工程目录内运行的本地
Skill，以及尚在设计中的能力。Agent 在安装前必须先按下表判断交付形态，不能把所有目录都复制到
全局 Skill 目录。

## 能力目录

| 能力 | 状态 | 交付形态 | 入口 | 安装方式 |
| --- | --- | --- | --- | --- |
| 考勤分析 | 可用 | 全局 Skill | [`skills/audit-attendance-workbook/SKILL.md`](skills/audit-attendance-workbook/SKILL.md) | 用 `skill-installer` 安装；安装后执行 `uv sync --locked` |
| 物料编码 | 可用 | 工程本地 Skill | [`projects/material-code/.codex/skills/material-code/SKILL.md`](projects/material-code/.codex/skills/material-code/SKILL.md) | 拉取 `projects/material-code`，在工程根执行 `uv sync --locked` |
| 报告生成 | 设计中 | 尚未决定 | [`projects/report-generation/README.md`](projects/report-generation/README.md) | 暂不可安装，不得把占位目录当作 Skill |

## 给 Agent 的安装路由

收到“安装这个仓库中的一个或几个 Skill”时：

1. 读取本 README 的能力目录。
2. 只把标记为“全局 Skill”的路径交给内置 `skill-installer`；可以在一次调用中传多个路径。
3. 对“工程本地 Skill”拉取完整分项工程，并在该工程根目录安装依赖和工作；不要复制到
   `$CODEX_HOME/skills`。
4. 跳过“设计中”的条目，并明确告诉用户它还不可安装。
5. 打开所选能力的入口文档，执行其中的前置检查。全局 Skill 安装后要告诉用户：它会在下一轮对话中可用。

可以直接把下面的提示交给 Agent：

```text
请阅读 https://github.com/TuKJet/functional-side-skills 的根 README，安装我指定的能力。
全局 Skill 使用内置 skill-installer；工程本地 Skill 拉取对应 projects 子目录并在工程根安装依赖。
不要安装“设计中”的条目，也不要提交业务数据、调试文件或 output 产物。
```

## 安装全局 Skill

当前可全局安装的条目只有 `audit-attendance-workbook`。让 Agent 执行：

```text
使用 $skill-installer 从 TuKJet/functional-side-skills 安装
skills/audit-attendance-workbook。然后进入安装目录执行 uv sync --locked，
报告安装结果，并提醒我该 Skill 会在下一轮对话中生效。
```

人工安装时，可调用 Codex 自带安装脚本：

```powershell
$codexRoot = if ($env:CODEX_HOME) {
  $env:CODEX_HOME
} else {
  Join-Path $env:USERPROFILE ".codex"
}

py "$codexRoot\skills\.system\skill-installer\scripts\install-skill-from-github.py" `
  --repo "TuKJet/functional-side-skills" `
  --path "skills/audit-attendance-workbook"

Set-Location "$codexRoot\skills\audit-attendance-workbook"
uv sync --locked
```

安装多个全局 Skill 时，在同一个 `--path` 后依次列出多个仓库路径。安装器不会覆盖已存在的同名
目录；更新前应由用户决定保留、备份还是移除旧版本。私有仓库使用运行环境已有的 Git/SSH 凭据，
不要把令牌写进文档、命令日志或对话正文。

## 拉取工程本地 Skill

物料编码依赖同一工程中的配置、SQLite 镜像和运行状态，不能脱离工程单独安装。可以拉取整个仓库：

```powershell
git clone git@github.com:TuKJet/functional-side-skills.git
Set-Location ".\functional-side-skills\projects\material-code"
uv sync --locked
```

只需要该分项时使用 sparse checkout：

```powershell
git clone --filter=blob:none --sparse `
  git@github.com:TuKJet/functional-side-skills.git material-code-workspace
Set-Location ".\material-code-workspace"
git sparse-checkout set projects/material-code
Set-Location ".\projects\material-code"
uv sync --locked
```

随后从 `projects/material-code` 作为工作目录启动或打开 Codex。Agent 会读取
`.codex/skills/material-code/SKILL.md`；首次业务处理前仍需由用户提供或明确选择权威数据源。

## 仓库约定

- `skills/` 只放可独立安装到全局 Skill 目录的自包含能力。
- `projects/<name>/.codex/skills/` 放依赖项目文件、数据或状态的工程本地 Skill。
- 未定型能力先放在 `projects/`，在交付形态确定前不创建 `SKILL.md`。
- 业务 Excel、SQLite 数据库、备份、运行状态、输出、虚拟环境、缓存和临时调试文件均不进入 Git。
- 每个可用 Skill 的 `SKILL.md` 是 Agent 的具体执行规范；根 README 只负责发现、选择和安装路由。

## 开发验证

```powershell
Set-Location ".\skills\audit-attendance-workbook"
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .

Set-Location "..\..\projects\material-code"
uv sync --locked
uv run --locked pytest
```

考勤分析子项目使用其目录内的 [MIT License](skills/audit-attendance-workbook/LICENSE)。其他分项未在
本仓库根目录声明统一许可证。
