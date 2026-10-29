# Agent 技术契约

本页给 WorkBuddy 接管 Agent 和开发维护者；HR 不需要使用命令。所有 CLI 输出为 JSON：成功 `{"ok":true,"data":...}`，失败为非零退出码和 `{"ok":false,"error":...}`。

## 入口

```text
python scripts/hr.py --root ROOT --actor NAME COMMAND [--payload JSON | --input JSONFILE]
```

`ROOT` 必须是本机工作副本；NAS 通过 `remote_root` 供 sync 使用。常用命令：`init`、`snapshot`、`doctor`、`jd-put`、`candidate-add/update`、`application-add/update`、`review-queue`、`review-submit`、`ranking`、`interview-add`、`ledger-export/import`、`sync`、`sync-status`、`sync-resolve`。

setup 为每位使用者写入 Skill 目录的 `workspace.local.json`。Agent 先读取其中 `python` 和 `script` 的绝对路径并用它们执行命令；CLI 默认读取已安装 Skill 根目录中的此配置，允许省略 `--root`/`--actor`，`sync` 的远端也由配置补齐。`--config PATH` 可显式指定配置，`--root`/`--actor` 可覆盖默认值。不要用系统 Python 猜测私有依赖环境。

## 评审约束

Agent 按 JD criteria 逐项给出证据、`rating` 0–4 或未知；有分时 quote 必须存在于候选人的原文。程序计算 `sum(rating / 4 * weight) / sum(weight)`，另给证据覆盖率；未知不等于不合格，全部未知为 `score:null` 且不排名。默认队列只给未审核、审核过期且未终止的投递；明确重审才包含已有效审核，并用新审核替代旧审核。原件或人工 OCR 文本变化会使审核过期。HR 复核和作最终决定。

## 记录与导入

记录为 `records/{candidates|jobs|applications|reviews|interviews}/{id}/{version}-{uuid}.json`，索引为原子 `catalog.json`，历史为 `history/{revision}-{uuid}.json`。原件为 `pool/{candidate_id}/{sha256}.{ext}`，附件为 `attachments/{uuid}/{sha256}.{ext}`。版本写入不可覆盖；冲突必须返回错误或冲突对象，不静默覆盖。

## 同步契约

`sync {remote_root, initialize_remote?:bool}`：首次远端不存在必须显式 `initialize_remote:true`；从已有远端首次 clone 必须使用空本地目录。`sync-status {}` 只比较本地和上次同步基线，不联网检查同事是否有更新；只有实际 `sync` 成功才确认本次交换完成。`sync-resolve {remote_root, resolutions:[{kind,id,local_sha256,remote_sha256,take:'local'|'remote'}]}` 只接受人工选择后的双方哈希，禁止用 `application-update` 解决同步冲突。三方合并可合并不同记录，同记录冲突不覆盖任何一侧；Excel 导出文件不上传。

## 桌面看板与内部 API 边界

Rust EXE 启动本地 HTTP 服务，并在原生 Windows 窗口内通过 WebView2 内嵌只读看板；该 HTTP 服务只监听 loopback，供桌面窗口和健康检查访问。看板展示总览、搜索、简历详情、审核证据、面试评价和同步状态，可触发同步。看板不得提交评审或提供 CRUD/技术设置表单；所有写入经 WorkBuddy 调用 CLI。关闭主窗口结束本次服务，重复启动复用并激活已有窗口。
