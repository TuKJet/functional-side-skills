# 清力物料编码管理

ERP 物料清单 Excel（权威数据源）↔ SQLite 镜像库，支持模糊检索与新物料编码生成。本分项是
**工程本地 Skill**：必须保留 `pyproject.toml`、`.codex/skills/material-code/`、业务数据与运行状态之间的
目录关系，不能只把 Skill 文件夹安装到全局目录。

## 安装

从 monorepo 根目录进入本分项；也可以按根 README 的 sparse checkout 指令只拉取这个目录：

```powershell
Set-Location ".\projects\material-code"
uv sync --locked
```

不要复制旧电脑的 `.venv`、SQLite 数据库、申请表或 `output`。环境由 uv 重建，业务数据由用户在
本机提供或明确选择。随后以本目录为工作区启动 Codex，让 Agent 读取
`.codex/skills/material-code/SKILL.md`。

## 使用（工程内 Codex Skill：`.codex/skills/material-code/`）

```bash
# 1. 每份新申请都先检查状态，并询问用户选择数据库来源
uv run python ".\.codex\skills\material-code\scripts\workflow.py" status

# 2A. 用户明确回复“沿用现有数据库”后，记录一次性确认
uv run python ".\.codex\skills\material-code\scripts\workflow.py" use-existing --confirmed

# 2B. 或者，经用户明确确认后，只把新权威 Excel 第一工作表同步到 SQLite
uv run python ".\.codex\skills\material-code\scripts\workflow.py" sync "<权威总表.xlsx>" --confirmed

# 3. 消费上述一次性确认，为带表头的申请表创建隔离 job，预计算 Top5 候选
uv run python ".\.codex\skills\material-code\scripts\workflow.py" prepare "<申请.xlsx>"

# 4. 展示并提交用户明确选择；只写独立结果 Excel，不写正式 SQL
uv run python ".\.codex\skills\material-code\scripts\workflow.py" show 1
uv run python ".\.codex\skills\material-code\scripts\workflow.py" commit 1:2
```

`prepare` 不再默认沿用已有数据库。每份新申请都必须先通过 `use-existing --confirmed` 或一次成功的
`sync --confirmed` 获得与当前数据库指纹绑定的一次性确认；确认用过或数据库变化后必须重新询问用户。
开始同步新总表时，旧的数据库选择会立即失效；同步失败后也需要重新确认，不能静默退回旧选择。

完整执行规范、用户确认门禁和失败处理见
[`SKILL.md`](.codex/skills/material-code/SKILL.md)。

## 可移植性与硬约束

- 所有运行时业务脚本都位于 `.codex/skills/material-code/scripts`，不依赖 `.workbuddy`。
- 工程根从当前目录、父目录或 `MATERIAL_CODE_ROOT` 动态发现，不写死盘符。
- 子脚本拒绝直接执行，只接受 `workflow.py` 的内部调用上下文。
- 申请候选固定 Top5、固定排除停用物料；数据库来源确认和批量授权都是一次性代码门禁。
- 正式 SQLite 只允许由总表同步重建；申请处理没有直接插入正式 SQL 的入口。

## 编码规则

`新物料编码 J（10位）= 大类C(2) + 一级子类E(2) + 二级子类G(2) + 流水号(4, 0001~9999)`，`H = C+E+G`。
只读取权威 Excel 的第一个工作表；后续工作表不导入、不参与流水号计算。新编码只是预定值，
须回填第一工作表并重新同步后才正式生效。
