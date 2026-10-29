# Functional Side Skills

这是一个包含四个**独立工程**的 monorepo。每个顶层文件夹都拥有自己的实现、依赖、测试、安装说明
和输出隔离规则；根目录只负责告诉 Agent 有哪些工程以及下一步该读哪个文件。

## 工程目录

| 工程 | 当前状态 | 工程入口 |
| --- | --- | --- |
| 考勤分析 | 可用；可全局安装 | [`考勤分析/README.md`](考勤分析/README.md) |
| 物料编码 | 可用；工程本地 Skill | [`物料编码/README.md`](物料编码/README.md) |
| 报告自动生成 | 可用；可全局安装 | [`报告自动生成/README.md`](报告自动生成/README.md) |
| 简历管理 | Skill + Excel 台账 + Rust/Web 看板 | [`简历管理/README.md`](简历管理/README.md) |

## 一句话安装报告生成 Skill

把下面这一句话发送给 Agent：

```text
请读取 https://github.com/TuKJet/functional-side-skills/blob/main/报告自动生成/README.md，使用内置 $skill-installer 从 TuKJet/functional-side-skills 的 报告自动生成/skills/generate-process-report 路径安装报告生成 Skill，按 README 安装依赖，完成后告诉我它将在下一轮对话中可用。
```

## Agent 必须遵循的读取顺序

当用户要求查看、安装、使用或开发其中一个或多个能力时：

1. 用上表确认用户选择的工程。
2. 进入每个被选中的顶层文件夹，完整读取该工程的 `README.md`。
3. 再按工程 README 指向的位置读取对应 `SKILL.md`、规则或开发说明。
4. 严格执行该工程自己的安装方式；不要根据根目录结构猜测它应当全局安装还是本地运行。
5. 同时选择多个工程时逐个处理，分别报告每个工程的状态和安装结果。
6. 对标记为“设计中”的工程只读取设计说明，不创建或安装占位 Skill。

可以直接把下面的提示交给 Agent：

```text
请阅读 https://github.com/TuKJet/functional-side-skills 的根 README。
对我选中的每个工程，进入对应顶层文件夹并完整阅读该工程的 README，
再严格按照该工程自己的说明安装、使用或继续开发。不要自行重组目录或推断安装形态。
```

## 仓库边界

- `考勤分析/`、`物料编码/`、`报告自动生成/`、`简历管理/` 保持为一等、独立的工程根。
- 全局 Skill 或工程本地 Skill 的差异只影响工程内部布局，不改变顶层工程目录。
- 跨工程不共享虚拟环境、运行状态、业务数据库或输出目录。
- 业务 Excel、SQLite 数据库、备份、运行状态、生成输出、缓存和临时调试文件不进入 Git。
- 开发某个工程时，从该工程 README 开始；根 README 不复制其具体安装和业务规则。
