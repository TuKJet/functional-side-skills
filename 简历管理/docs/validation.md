# 开发验收记录

验收日期：2026-10-29。全部为合成资料；没有接入公司真实 NAS 或真实候选人资料。

## 最新正式发布验收

- 源码已推送 `main`，客户端构建提交：`0a50d5f3e9ffac0124666ff92e7243feb771dd31`。
- [GitHub Actions 40854599632](https://github.com/TuKJet/functional-side-skills/actions/runs/40854599632) 全部成功：Windows x64、Apple 芯片 macOS、正式发布。仓库原有 CI 同时通过。
- [Release hr-client-v0.3.0-run3](https://github.com/TuKJet/functional-side-skills/releases/tag/hr-client-v0.3.0-run3) 是正式非草稿版本，提供单个 Windows EXE、Apple 芯片 DMG 和各自 SHA256；不提供 Intel Mac。上传缺文件会失败，发布前后逐一检查四个必需文件。
- Windows EXE 已从该 Release 真实下载到独立临时目录，SHA256 为 `8e13e4ab7dc752f1978db1343140fdb1ea300367024bbb0d7025f5a49a42c64d`；`--check-runtime` 成功，`--headless` 自动读取隔离的用户配置并成功调用安装 Skill 的 snapshot。没有启动窗口，也没有创建桌面快捷方式。
- 真实 Skill 更新器在隔离副本完成从 `d31b630` 到 `905c186` 的 GitHub 升级；再次 check 返回当前已是最新且无本地修改。此次验证不覆盖用户业务目录。
- Windows 更新器的 CRLF/LF/BOM 差异误判已修复，真实依赖版本变化仍拒绝。Windows/Apple 芯片客户端均已在对应 runner 编译、进行无界面接口与安装检查；Mac 实屏交互、Gatekeeper 和公司 WorkBuddy/NAS 仍需目标环境验收。
- macOS 仅使用 ad-hoc 签名，没有 Developer ID 签名或公证。以下小节保留此前本地开发验收记录，旧 ZIP 不作为当前用户下载入口。

## 可复现检查

工程根目录：

```powershell
$env:PYTHONUTF8='1'
$env:HR_WEB_HTTP_TESTS='1'
python -m unittest discover -s tests -v
node desktop/web/tests.cjs
```

desktop 目录先执行 `cargo fmt --check`、`cargo check`、`cargo test`、`cargo build`。HTTP 集成测试需要允许 loopback socket。

结果：49 项 Python/真实 HTTP 测试、12 项 Web 逻辑检查、4 项 Rust 测试均通过。Python 编译检查、Rust 格式检查和 Windows x64 release 构建通过。最终分发 EXE 另行通过 4 项无界面 HTTP 测试；这些测试需要允许本机 loopback 连接。

Windows 原生窗口已实际启动并检查中文显示、导航与独立窗口。进程集成测试的并发复用、关闭退出、残留记录重启和 headless 隔离断言通过，但最后清理 WebView2 缓存目录时因文件仍被运行时占用报错，因此不计为整项通过。测试已改为在忽略目录保留诊断缓存，尚未重跑。手动复现命令（会打开窗口）：

```powershell
$env:HR_RELEASE_TESTS='1'
python -m unittest discover -s desktop/tests -v
```

覆盖原件提取、中文目录、JD 版本、证据引用、缺证据不评分、显式重审、审核过期、二面附件、Excel 回写与原子冲突、三客户端共享 Hub、锁获取前发生的远端更新、同步中断重试、双方冲突选择版本校验、路径越界、跨源请求拒绝和 250KB 子进程输出无死锁。

## 安装与客户端

- 在项目临时目录创建全新私有虚拟环境，离线安装 openpyxl 3.1.5、pypdf 6.21.4；安装副本 CLI 健康检查通过。
- 先前浏览器验收加载 6 位合成候选人、3 个岗位、4 份审核、2 份面试评价；目前默认入口已改为 Rust + WebView2 独立 Windows 窗口，不再打开默认浏览器。
- 实测简历池浏览、岗位名称检索、月份筛选、候选人抽屉、评分原文证据、二面评价、模拟 Hub 同步。
- 原生窗口已检查概览、简历池和招聘需求，中文显示正常；截图保存在 `.runtime/招聘工作台-原生窗口.png`，截图早于本次紧凑布局。
- 紧凑布局已通过静态复核、JavaScript 检查和重新构建：主列表分页，招聘进度分为进行中/已结束，详情按标签和分页阅读。用户按 Esc 停止界面操作后未继续控制窗口，因此最新版紧凑布局尚未完成最终截图验收，不将静态检查等同于实屏验收。
- 预览和演示台账留在 `.runtime/`，不进入 Git。演示资料有明确标识。

## Windows 分发包与独立安装

- release EXE 为 Windows x64，HTML/CSS/JavaScript 已内嵌；运行不需要 Rust 或 `web/` 源文件。需要本机 Python 和 WebView2 Runtime。`--check-runtime` 已返回 `ok:true`、`desktop:embedded-webview`，本机 WebView2 为 156.0.4314.50。
- 一个独立 Agent 只读取 ZIP 内 README 和安装指南，在带空格与中文的全新目录完成离线 setup；没有读取开发实现来补全文档。
- 已安装 Skill 的配置可独立定位解释器、脚本、本地副本和操作者；`doctor` 返回 `healthy:true`、`snapshot` 返回 `ok:true`，发布 EXE 实际 HTTP 页面返回 200。
- WorkBuddy 导入 ZIP 内容核对通过，已安装 SKILL.md 和 hr.py 与导入包哈希一致；不包含 `.venv`、缓存或业务资料。
- 最终 `HR招聘工作台-Windows桌面版-v0.3.0-20261029.zip` 已在新的中文和空格目录独立解压，保留 EXE 原名，完整离线 setup 成功：receipt 为 `ok:true`、`sync:offline`，随后私有 Python 的 `doctor` 和 `snapshot` 均通过。安装脚本已修复 PowerShell 对 GUI EXE 输出管道过早关闭的问题，显式等诊断进程退出后读取结果。
- 本次 setup 生成 14 项 WorkBuddy 导入包，包含个人路径配置及更新器；不包含 `.venv`、`.update`、业务资料或备份。最终 EXE SHA256：`7CCADE9F4065291A7BCED721C0560F55E84B9F68AA91911CF6AD9E4AAA084349`。
- 另一次 setup 配置本地模拟 NAS，初始化与同步返回 `synced`。该测试不代表公司 SMB 网络已验收。

## Skill 更新器

- 10 项离线回归测试通过，覆盖检查、升级、回退、本地修改拦截、依赖变化拦截、恶意归档拒绝、更新中断和回退失败重试；独立代码审查通过。
- WorkBuddy 更新包排除运行时、更新备份和业务资料；升级/回退保留个人配置和工作副本。
- 首次发布前的 GitHub 只读检查曾返回 `upstream_missing`；目前源码已推送且真实升级已验证，结果见本页顶部最新发布记录。

## 部署边界

- 公司 NAS 地址尚未提供。真实 SMB 权限、断线行为、多人网络环境和 NAS 快照恢复需安装 Agent 现场验收。
- WorkBuddy 官方入口及本地任务要求已核对；本次验收了安装副本和导入包，尚未在真实 WorkBuddy 客户端上传验收，不宣称已经部署到同事电脑。
- 扫描件需要 OCR 和人工核对文本；旧 DOC、图片等可保存原件，但不伪装成已提取或已审核。
- 不自动决策录用、不自动解决同记录冲突、不强拆遗留写锁。
