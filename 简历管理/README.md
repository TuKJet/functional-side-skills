# HR 简历管理

这是一个不使用数据库的招聘协作工程：每位 HR 有自己的本地工作副本，Skill 负责维护和审核，Rust 客户端在 Windows / macOS 独立窗口内嵌看板，NAS Hub 用于多人交换进展。

**下载：[最新客户端](https://github.com/TuKJet/functional-side-skills/releases/latest)。Windows 是单个 `hr-resume-windows-x64.exe`；Mac 的“关于本机”显示 Apple 芯片选 `macos-aarch64.dmg`，显示 Intel 选 `macos-x86_64.dmg`。不用解压工程包。**

首次安装交给 Agent：Windows 读 [Windows 安装指南](docs/agent-install.md)，Mac 读 [macOS 安装指南](docs/macos-install.md)。后续客户端变更推入 main 后，GitHub 自动测试、编译并发布三个芯片安装包；发布流程和升级规则见 [客户端发布与升级](docs/client-releases.md)。

**职能同事双击 [使用说明.html](使用说明.html)，一页看完；也有 [文字版](docs/hr-user-guide.md)。安装 Agent 先读本页，再按 [安装指南](docs/agent-install.md) 执行。**

交给新 Agent 的话：

> 请从 TuKJet/functional-side-skills 获取“简历管理”工程，读取 README 和对应系统安装指南，帮我安装 HR Skill。路径由你选本机独立目录；先问我的姓名，NAS 路径不确定就留空。安装后自动下载并配置对应客户端，校验 doctor 和 snapshot，告诉我客户端从哪里打开，最后问我是否需要桌面快捷方式；只有我需要时才创建。再指导我在 WorkBuddy 本地任务中导入本机 Skill 包。未完成的环节请如实说明。

## 谁用哪个入口

| 人 | 入口 | 能做什么 |
|---|---|---|
| HR/职能同事 | Windows `招聘看板.exe` / Mac `招聘看板.app` | 看总览、搜索、简历详情、审核证据、二面评价、同步状态；点同步 |
| HR/职能同事 | WorkBuddy | 用自然语言导入简历、维护 JD/投递/面试、请求审核、回写 Excel、处理同步冲突 |
| 安装 Agent/IT | `scripts/setup.ps1`、`scripts/package-windows.ps1`、`desktop` | 安装 Skill、构建/配置 EXE、设置本地副本和可选 NAS Hub |

看板是本地只读 Web UI，不提供 CRUD 表单、技术设置或简历评审；评审由 WorkBuddy 按 JD 逐项引用证据，HR 复核并作最终决定。日常如发现规则或功能异常，请让 WorkBuddy 检查/升级 HR Skill；数据接口仍只监听 loopback，由桌面窗口内嵌访问。

## 从哪里运行

以下命令适用于 Windows；macOS 直接按 [Mac 安装步骤](docs/macos-install.md) 使用 Python 安装脚本，无需 PowerShell。

源码或发布包解压目录由安装 Agent 实际选择；以下示例先进入那个目录，不要照抄别人的盘符：

```powershell
$packageRoot = Read-Host '源码或发布包的实际解压目录'
Set-Location $packageRoot
$skillTarget = Read-Host 'WorkBuddy Skill 目标目录'
$localRoot = Read-Host '本机工作副本目录'
$actor = Read-Host '使用者姓名'
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -File '.\scripts\setup.ps1' `
  -SkillTarget $skillTarget -LocalRoot $localRoot -Actor $actor
```

Windows 用户只下载一个 EXE，HTML/CSS/JavaScript 已嵌入其中。Python、Skill、个人配置由安装 Agent 准备，不要求用户解压源码；EXE 使用 WebView2 内嵌窗口。`package-windows.ps1` 的完整 ZIP 仅供开发和 CI 验证安装，不是对职能同事的下载入口。

## 安装前提

安装 Agent 的 Windows 电脑必须是 x64，并具备 Python 3.11+（含 `venv` 与 `pip`，pip 22.3+）和 PowerShell 7。先验证：

```powershell
python --version
python -m pip --version
python -m venv --help
& 'C:\Program Files\PowerShell\7\pwsh.exe' --version
```

任一项失败就交给安装 Agent 补齐；HR 不需要构建 Rust。

## 安装只需要四项信息

安装 Agent 向 HR 收集：Skill 目标目录、每人的本地工作副本目录、显示姓名、可选 NAS Hub 路径。`setup.ps1` 会安装私有依赖、初始化本地副本、保存配置，并在指定 NAS 上同步；首次空 NAS 必须加 `-InitializeRemote`，临时离线加 `-SkipSync`。安装目标必须不存在，不覆盖旧安装；已有 Hub 的新电脑先用空本地目录 clone。日常断网可工作，恢复后同步。

setup 会生成每位使用者独立的 `.runtime\manage-hr-resumes-workbuddy.zip`，不能互传，也不能上传未配置源码包。个人版 WorkBuddy 从左侧“技能 → 添加技能 → 上传技能”导入；企业版入口为“专家·技能·连接器”。[官方导入说明](https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)未承诺固定扫描目录或统一包扩展名，本项目尚未完成真实 WorkBuddy/NAS 导入验收；不接受 ZIP 时按客户端允许格式解压导入，或让**本地任务**显式读取安装目录的 `SKILL.md`。

## 验收与升级

setup 完成后，使用已经配置的本地工作副本运行 `doctor` 和 `snapshot`，预期输出 `ok:true`，且 `doctor` 的 `healthy:true`；再双击 `招聘看板.exe`，在独立窗口中查看同一个本地副本。关闭主窗口会结束本次服务；再次双击会激活已有窗口。`create_demo.py` 只用于开发培训，不是 HR 安装步骤。正式业务数据、NAS、备份、虚拟环境和运行状态不进 Git。

升级使用新版本目录并先验收；不要移动或删除业务工作副本、NAS Hub、原件、评价附件和 Excel 备份。WorkBuddy 导入说明见安装文档；`desktop\README.md` 仅是源码开发说明，不随发布包作为必读文件。完整说明：

- [给 HR 的一页说明](docs/hr-user-guide.md)
- [安装 Agent / WorkBuddy 接管](docs/agent-install.md)
- [HR Skill 更新](docs/skill-updates.md)
- [macOS 安装](docs/macos-install.md)
- [客户端发布与升级](docs/client-releases.md)
- [NAS 同步和恢复](docs/nas-operations.md)
- [Agent 技术契约](docs/api.md)
