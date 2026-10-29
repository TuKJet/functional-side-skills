# 数据和审核约定

## 记录布局

根目录下由程序维护 `records/{candidates|jobs|applications|reviews|interviews}/{id}/{version}-{uuid}.json`、`history/{revision}-{uuid}.json`、`catalog.json` 和 `.write.lock`。原始简历放在 `pool/{candidate_id}/{sha256}.{ext}`，面试评价附件放在 `attachments/{uuid}/{sha256}.{ext}`；不把业务文件提交 Git。每条记录含稳定 `id`、整数 `version`、`updated_at` 和 `updated_by`。版本文件带 UUID，崩溃遗留文件不会覆盖后续版本；`catalog.json` 是唯一当前索引并原子更新，不能直接编辑。

## 主要字段

- candidate：`name`, `source`, `notes`, `resume.path`, `resume.sha256`, `resume.text`, `resume.text_sha256`, `resume.text_status`, `resume.transcript`（可选人工 OCR 原件）。新增时 `resume_path` 必填；更新可用 `text_path` 提供人工 OCR 文本。原始简历或 OCR 转录内容变化都会让相关审核过期。
- job：`department`, `title`, `month`（YYYY-MM）, `headcount`, `status`（`招聘中`/`暂停`/`结束`）, `description`, `criteria[]`。criterion 为 `id`, `label`, `weight>0`, `required`。
- application：`candidate_id`, `job_id`, `month`, `status`, `owner`, `notes`, `next_action_date`。
- review：`application_id`, `application_version`, `job_version`, `resume_sha256`, `text_sha256`, `score`, `coverage`, `strengths[]`, `risks[]`, `questions[]`, `criteria[]`, `reviewer`, `supersedes_review_id`（显式重审时填写）。criterion 审核含 `criterion_id`, `rating` 0–4 或 null, `evidence`, `reason`；有分项的 evidence 必须是 `resume_text` 原文，null 表示未证实；快照计算 `is_stale`。
- interview：`application_id`, `round`（必须为 2）, `evaluator`, `summary`, `recommendation`, `attachment_path`, `attachment.sha256`, `interview_date`。

允许的投递状态：`待筛选`、`待复核`、`初面`、`二面`、`待决定`、`录用`、`未录用`、`已撤回`。

## 审核记录示例

```json
{
  "application_id": "从 review-queue 原样复制",
  "application_version": 1,
  "job_version": 1,
  "resume_sha256": "从 review-queue 原样复制",
  "text_sha256": "从 review-queue 原样复制",
  "criteria": [
    {"criterion_id":"c1","rating":3,"evidence":"曾负责电机机械设计","reason":"有相关职责的自述，项目规模待面试核实"},
    {"criterion_id":"c2","rating":null,"evidence":"","reason":"未找到对应项目经验，需面试核实"}
  ],
  "strengths":["有 JD 所需工具的项目经验"],
  "risks":["关键业务规模未在简历中说明"],
  "questions":["请说明项目规模和本人负责范围"],
  "reviewer":"HR姓名"
}
```

这是结构示例，不可原样提交。所有版本、哈希、ID 从本次 `review-queue` 对应项复制；`criterion_id` 对照该项 JD 的 `criteria`，必须覆盖每项。示例第一条仅在简历确实含有“曾负责电机机械设计”时成立；证据字符串不要加“第几段”等简历没有的文字。有效审核再次审核时，另填当前审核 ID 为 `supersedes_review_id`。

缺失信息写“未找到证据”，不要推断为不具备。必须保留简历和人工 OCR 文本哈希以及 JD、投递版本，才能识别审核是否过期。`score` 由程序按 `Σ(rating / 4 × weight) / Σweight × 100` 计算；未知项（rating 为 null）贡献 0，但另列 `coverage = Σ已评分权重 / Σ全部权重 × 100`，不能把未知当作不合格。全部未知时 `score=null`，不参与 ranking。过期或无文本记录不参与 ranking。

## NAS 协作

本地工作副本路径由每台电脑配置；NAS `remote_root` 例如 `\\server\share\hr-resumes` 只由 `sync` 使用，不让多人直接编辑同一远端目录。sync 用 `.sync/state.json` 保存三方基线，记录不可变，并以临时文件加替换方式更新 `catalog.json`；每次索引提交同时写入 `history/{revision}-{uuid}.json`。检测到同记录冲突时保留双方且不覆盖，交给 WorkBuddy 协助合并。Excel 导出临时文件不参与同步。锁不自动过期，管理员确认没有写入者后再人工处理，程序不会自动拆锁。
