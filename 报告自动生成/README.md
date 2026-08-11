# 报告自动生成

本分项提供可安装 Skill，把 MES 导出的 Excel 转换为可审阅 Markdown，并在明确批准后转换为可直接交付客户的单文件 HTML 工艺报告。

本文件夹就是报告生成的独立工程根目录。后续设计、实现、测试、模板和 Skill 都在本目录内继续
开发，不移动到仓库根目录的其他分类目录。

## 当前结论

- 状态：`available`，可全局安装。
- 交付形态：`skills/generate-process-report/` 下的可全局安装、自包含 Skill。
- 业务报告、调试输入、渲染预览和生成结果必须留在本地，不能提交到 Git。

## 首个报告类型

### 真实触发示例

1. “把这份 MES 导出表生成客户可查看的工艺 HTML 报告，并展示每段工艺的代表图片。”
2. “读取设备参数详情和文件绝对路径，把参数转成列表，从附件里抽取前中后图片。”
3. “将导出的 Excel 打包成一个离线可打开的单文件报告，`display` 开头的图片必须优先展示。”

### 不应触发的反例

- 只统计 Excel 中的工时、片数或贵金属用量，不需要生成图文工艺报告。
- 编辑已有 Word、PowerPoint 或 PDF 报告。
- 对图片做缺陷识别、测量或质量判定；本能力只做确定性抽取、排序和展示。

### 输入与输出契约

- 输入：`.xlsx` 文件；必须包含“工步描述”“设备参数详情”和“文件绝对路径”表头，其他工艺上下文列可选。
- 参数：优先解析标准 JSON；失败时尝试 Python 字面量和简易键值文本，仍失败则保留原文并记录警告。
- 附件：支持 HTTP(S)、本地文件和多级目录中的图片；原生支持 ZIP/TAR，RAR/7z 采用可用依赖降级处理。
- 图片：每个工艺段最多 10 张；优先 `display` 前缀，再从自然排序后的前、中、后位置均匀抽样。
- 顺序：严格保持 Excel 数据行顺序，并以“工步描述”作为每段设备参数表的标题。
- 空附件：某行“文件绝对路径”为空时只输出设备参数表，不生成图片标题或空占位。
- 输出门禁：第一阶段必须先生成 `.md` 和同名资源目录并返回用户审阅；只有随后取得明确同意，第二阶段才能生成不依赖网络、资源全部内嵌的单个 `.html` 文件。
- HTML 导航：页面顶部单独一行展示多列“工步描述”；默认显示第一排，点击按钮展开其余排，选择后回到第一排并高亮当前段，不使用横向滚动。
- 模板：Skill 自带客户交付风格的离线 HTML 模板，不依赖业务工程或外部 CDN。
- 隐私：临时下载和解压目录在运行结束后清理；默认不在报告中展示内部绝对路径或下载 URL。
- 内部字段：“产品规格”在 Python 建立列映射前即被拒绝，脚本不访问、不解析、不记录也不输出该列值。
- 失败行为：缺少必需表头时立即失败；单个参数、附件、压缩包或图片失败时记录到对应工艺段并继续。

## 安装

### Agent 一句话安装

把下面这一句话发送给 Agent：

```text
请读取 https://github.com/TuKJet/functional-side-skills/blob/main/报告自动生成/README.md，使用内置 $skill-installer 从 TuKJet/functional-side-skills 的 报告自动生成/skills/generate-process-report 路径安装报告生成 Skill，按 README 安装依赖，完成后告诉我它将在下一轮对话中可用。
```

Agent 应使用 Codex 内置 `skill-installer` 安装仓库路径
`报告自动生成/skills/generate-process-report`，而不是复制整个仓库。安装器会把 Skill 写到
`$CODEX_HOME/skills/generate-process-report/`；目标目录已存在时必须停止并由用户决定如何更新，不能覆盖。

安装完成后，在目标 Skill 内创建独立虚拟环境并安装锁定依赖：

```powershell
py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

安装完成后告诉用户：新 Skill 会在下一轮对话中可用。不要把 `.venv`、业务 Excel、生成报告或资源
目录提交到 Git，也不要把 GitHub 令牌写入 README、命令日志或对话正文。

## 使用

第一阶段只能生成 Markdown：

```powershell
& .\.venv\Scripts\python.exe .\scripts\generate_report.py input.xlsx --output output\report.md
```

将 Markdown 和 `report_assets/` 返回用户审阅。只有用户随后明确同意，第二阶段才可运行：

```powershell
& .\.venv\Scripts\python.exe .\scripts\generate_report.py input.xlsx --output output\report.html --format html --approved-html
```

## 验证

在本工程根目录执行：

```powershell
python -m unittest discover -s skills\generate-process-report\tests -v
python "$env:CODEX_HOME\skills\.system\skill-creator\scripts\quick_validate.py" skills\generate-process-report
```

Skill 校验器需要 `PyYAML==6.0.2`；项目运行依赖和测试依赖均锁定在 Skill 目录。
