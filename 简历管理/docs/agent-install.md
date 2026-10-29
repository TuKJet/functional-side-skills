# 安装 Agent / WorkBuddy 接管指南

本文件给负责配置的 Agent 或 IT 同事。HR 不需要阅读本文件，也不需要知道 Python、目录版本或命令参数。

本页命令适用于 Windows。**macOS 请改读 [macOS 安装指南](macos-install.md)**，不要在 Mac 执行 PowerShell 安装命令。两个系统都从 [GitHub Release](https://github.com/TuKJet/functional-side-skills/releases/latest) 下载对应客户端。

## 安装前提（先检查）

目标电脑必须是 Windows x64，并安装 Python 3.11+（包含 `venv` 和 `pip`，pip 22.3+）、PowerShell 7 以及 WebView2 Runtime。先运行包内 `招聘看板.exe --check-runtime`，应返回 `ok:true` 和 `webview2` 版本。缺失时按 [微软官方 WebView2 分发说明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution) 安装 Evergreen Runtime；setup 也会在安装前执行此检查。安装 Agent 在 PowerShell 7 中执行：

```powershell
python --version
python -m pip --version
python -m venv --help
& 'C:\Program Files\PowerShell\7\pwsh.exe' --version
```

命令任一失败，先补齐环境再 setup；HR 不需要构建 Rust。

## 你需要向 HR 收集的 4 项信息

1. **WorkBuddy Skill 目标目录**：由安装 Agent 选择的本机目录。
2. **每人的本地工作副本**：由安装 Agent 选择的独立本机目录；不要让多人直接编辑同一个 NAS 业务目录。
3. **使用者姓名**：写入操作记录的显示名。
4. **NAS Hub（可选）**：公司共享目录，例如 `\\NAS\HR-Hub`；不确定时先留空，之后再配置。

## 从项目源目录安装与配置

在源码目录或发布 ZIP 的实际解压目录打开 PowerShell 7，先进入该目录，再填入实际目录执行 setup（不要把示例盘符当成固定路径；路径含空格时保留单引号）：

```powershell
$packageRoot = Read-Host '源码或发布包的实际解压目录'
Set-Location $packageRoot
$skillTarget = Read-Host 'WorkBuddy Skill 目标目录'
$localRoot = Read-Host '本机工作副本目录'
$actor = Read-Host '使用者姓名'
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -File '.\scripts\setup.ps1' `
  -SkillTarget $skillTarget -LocalRoot $localRoot -Actor $actor
```

`SkillTarget`、`LocalRoot`、`Actor` 必填；`RemoteRoot` 可选，`Python` 默认 `python`，可传解释器完整路径。setup 会调用 install 建立私有 `.venv`，执行本地 `init`/`doctor`，写入工程 `.runtime\client-config.json` 和 Skill 内 `workspace.local.json`，供 Skill/Agent 后续读取。成功回执保存在 `.runtime\setup-result.json`。目标目录必须不存在；不会覆盖已有 Skill。配置完成后保留解压目录和 Skill 安装目录原位置，不能只把 EXE 拖到桌面；桌面应创建快捷方式。

下面的安装分支**任选一次**，不要连续执行：普通离线 setup、首次空 NAS 初始化、已有 NAS 接入、以及“已安装 Skill 后补配 NAS”是互斥场景。若已经安装 Skill，后续只配 NAS 必须使用 `-UseExistingSkill`，否则会因目标目录已存在而停止。

首次空 NAS 明确授权初始化：

```powershell
$remoteRoot = Read-Host 'NAS Hub UNC 路径'
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -File '.\scripts\setup.ps1' `
  -SkillTarget $skillTarget -LocalRoot $localRoot -Actor $actor `
  -RemoteRoot $remoteRoot -InitializeRemote
```

已有 NAS 的新电脑使用空的 `LocalRoot`，不加 `-InitializeRemote`；临时无网加 `-SkipSync`。若明确复用已验收安装，使用 `-UseExistingSkill`，但该目录必须已有私有 `.venv`，setup 不会替你修复它。

已装 Skill 后才补 NAS 配置时，保留原来的 `SkillTarget` 和 `LocalRoot`，执行：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -File '.\scripts\setup.ps1' `
  -SkillTarget $skillTarget -LocalRoot $localRoot -Actor $actor `
  -RemoteRoot $remoteRoot -UseExistingSkill
```

没有网络时，准备离线 wheelhouse 后执行 setup（它会把 `-Wheelhouse` 传给 install）：

```powershell
$wheelhouse = Read-Host '离线 wheelhouse 目录'
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -File '.\scripts\setup.ps1' `
  -SkillTarget $skillTarget -LocalRoot $localRoot -Actor $actor `
  -Wheelhouse $wheelhouse -SkipSync
```

依赖只装在 Skill 私有虚拟环境，不改系统 Python。底层 `install.ps1` 仍支持 `-SkipDependencies`（同 `-NoInstall`），但 setup 是标准路径。

## 自动下载单文件客户端

安装 Agent 从 GitHub 获取本工程源码后运行 setup。源码不含已编译客户端时，setup 自动调用已安装 Skill 的 `hr_client.py download`，下载单个 Windows EXE 并校验 SHA256，保存到 `%LOCALAPPDATA%\HRResume\clients\<发布版本>\`。客户端读取 `%LOCALAPPDATA%\HRResume\client-config.json`。源码或测试包已有 EXE 时直接使用；`-SkipClientDownload` 只供离线配置/测试，使用它不得声称客户端已就绪。

成功后告诉用户回执中的 `executable` 路径，并询问“是否需要在桌面创建招聘工作台快捷方式？”用户同意才运行以下命令，拒绝则结束安装，不重复询问。单独 EXE 不要求用户构建 Rust 或解压工程；Python、Skill 和 WebView2 由 Agent 配好。

```powershell
& $venvPython (Join-Path $skillTarget 'scripts/hr_client.py') download --skill-root $skillTarget
# 仅在用户同意后执行；$executable 为 download 返回的已验证路径。
& $venvPython (Join-Path $skillTarget 'scripts/hr_client.py') shortcut --executable $executable
```

源码构建/CI 使用的完整 ZIP 仍可包含 EXE、Skill、文档与 wheelhouse，但不是用户下载的必经流程。WorkBuddy 的 Skill 导入包仍是 ZIP，它由 Agent 自动生成和引导导入，与客户端下载是两件事。

## 首次验收

setup 已经初始化本地工作副本；验收时直接检查刚才配置的 `$localRoot`，不要另建临时 root 再误以为看板会指向它：

```powershell
$py = Join-Path $skillTarget '.venv\Scripts\python.exe'
$entry = Join-Path $skillTarget 'scripts\hr.py'
& $py $entry --root $localRoot --actor $actor doctor
& $py $entry --root $localRoot --actor $actor snapshot
```

预期两次输出均为 `ok:true`，其中 `doctor` 的 `healthy:true`；再双击发布包的 `招聘看板.exe`，在独立窗口中确认看到同一个 `$localRoot`。`create_demo.py` 仅供开发培训，不是 HR 安装必选步骤。看板只读；新增、修改、审核、Excel 回写由 WorkBuddy 完成。

## 配置启动与交接

setup 时传入本地副本、姓名和可选 NAS Hub；配置保存到工程 `.runtime\client-config.json` 和已安装 Skill 的 `workspace.local.json`。源码目录可双击 `启动招聘工作台.cmd`，发布包则双击 `招聘看板.exe`；两者都沿用已保存配置。HR 不在网页输入这些值。

日常流程是：WorkBuddy 在本地执行维护和审核 → 独立窗口看板查看 → 点击同步。首次使用空的 NAS Hub 必须由 Agent 明确授权初始化；从已有 Hub 换新电脑必须使用空的本地目录先拉取，不能把两个已有目录直接拼在一起。断网时只做本地工作，恢复后再同步。

## 导入 WorkBuddy

setup 为每位使用者生成 `.runtime\manage-hr-resumes-workbuddy.zip`。该包包含 Skill 的 `SKILL.md`、`requirements.txt`、`scripts`、`references`、`agents` 和本机生成的 `workspace.local.json`，不包含虚拟环境、缓存或简历数据。配置中的 Python、脚本和本地副本路径是该电脑上的绝对路径；包不能在不同电脑之间互传，不能上传未配置的源码包冒充已安装。

个人版 WorkBuddy 的已知入口是左侧 **技能 → 添加技能 → 上传技能**，上传后确认“已安装本地技能”。企业版入口名称为 **专家·技能·连接器**。官方没有公布固定扫描目录或统一包扩展名，不要承诺自动扫描；本项目尚未完成真实 WorkBuddy 导入验收。若客户端不接受 ZIP，解压后按客户端允许的本地技能格式导入；若只支持本地任务访问目录，让 Agent 明确读取安装目录的 `SKILL.md` 并调用它，这属于显式调用，不是自动注册。

官方参考：[Skills Market](https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)、[本地任务访问限制](https://cloud.tencent.com/document/product/1831/138797)、[Skill 文档](https://open.workbuddy.cn/docs/skill)。真实 NAS 和 WorkBuddy 导入均需安装 Agent 在目标环境中复核，文档不将其写成已验收事实。

可直接复制给新 Agent 的安装提示：

```text
请先读取本包 README.md 和 docs/agent-install.md，不要猜 WorkBuddy 目录或 NAS 路径。先确认 Windows x64、Python 3.11+（venv、pip）和 PowerShell 7，选择本机独立的 SkillTarget 和 LocalRoot，再询问我的姓名与可选 NAS 路径。进入本包实际解压目录后执行 setup.ps1，选择正确分支。完成后汇报 setup-result.json 回执位置、doctor 的 ok:true 与 healthy:true、snapshot 结果、每人配置包路径和招聘看板.exe 地址；WorkBuddy 上传或本地任务显式读取 SKILL.md 后，再报告导入结果。没有真实导入、NAS 和界面验收证据时，不要声称该环节安装完成。
```

WorkBuddy 执行命令时可能要求确认访问本机 Python、文件夹或 NAS 的权限。这是 WorkBuddy 自身的权限流程；本工程看板没有业务录入或评审弹窗。

## 业务安全边界

- 简历原件、人工转录文字、逐项证据和二面评价保留在工作副本；Skill 使用不可变版本记录。
- 评分只对照 JD 工作要求；不使用年龄、性别、婚育、照片、民族、健康等无关属性。
- 没有证据是“待核实”，不是“不合格”；全部未知时不做排名。HR 复核并作最终决定。
- 同一记录在本机和 NAS 同时变化时不覆盖任何一方。Agent 展示双方，待 HR 选择后再调用冲突解决流程，不能用普通进度修改冒充解决。
- 导出的 Excel 台账是工作文件，不随 NAS 同步；回写后的记录才是协作内容。

## 升级、回滚和常见错误

安装目标已存在：换新的版本目录，不要删除旧目录。依赖安装失败：确认 Python 3.11+ 或改用离线 wheelhouse；不要把依赖装到系统目录。窗口打不开：确认 EXE、配置中的本地副本和 WebView2 Runtime 存在，再查看 `.runtime` 日志。同步冲突：保留冲突报告，不删除 `.sync` 或任一版本，交给 Agent 协助。

升级只替换 Skill/程序安装目录，不触碰业务工作副本、NAS Hub、简历原件、评价附件和 Excel 备份。
