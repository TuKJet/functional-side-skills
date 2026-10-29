# macOS 安装：交给 Agent 完成

HR 只需说：“请按这份文档安装招聘工作台。”完成配置后双击 **招聘看板.app** 查看，录入与审核继续交给 WorkBuddy。

## 选择下载

打开 [最新发布](https://github.com/TuKJet/functional-side-skills/releases/latest)：Apple 芯片选 `hr-resume-macos-aarch64.dmg`，Intel 选 `hr-resume-macos-x86_64.dmg`。目前支持 macOS 11 及更新版本。Agent 安装 Skill 时自动下载并校验 DMG，挂载后将“招聘看板”拖入“应用程序”。用户不需要下载工程 ZIP。

目前仅有 ad-hoc 签名，没有 Apple Developer 签名或公证。首次打开若被拦截，确认下载来源后按 [Apple 官方说明](https://support.apple.com/zh-cn/102445) 在系统设置的“隐私与安全性”允许该应用；不需要关闭 Gatekeeper，也不要运行全局取消安全检查的命令。

## Agent 安装步骤

先检查 `python3 --version`、`python3 -m pip --version`、`python3 -m venv --help`。需要 Python 3.11+ 和 pip 22.3+；缺少时安装官方 Python。客户端使用系统 WebKit，不需要 WebView2、Rust 或 PowerShell。

只需确认使用者姓名、Skill 安装目录、本地工作副本及可选 NAS。NAS 是 Finder 已连接共享空间的本机路径，例如 `/Volumes/HR-Hub`；本地工作副本放在本人磁盘，不与 NAS 路径重叠。未确定 NAS 时先留空。

Agent 从 GitHub 获取“简历管理”工程源码，在工程目录执行（替换姓名及实际路径）：

```bash
python3 scripts/setup_macos.py \
  --skill-target "$HOME/Library/Application Support/HRResume/skills/manage-hr-resumes" \
  --local-root "$HOME/Documents/HR招聘资料" \
  --actor '实际使用者姓名'
```

依赖安装到 Skill 的 `.venv`；不改系统 Python 包。源码安装会下载 Python 依赖；离线安装可传 `--wheelhouse`，无网络且尚无客户端时可用 `--skip-client-download` 先完成配置，但不能声称客户端已经可用。不覆盖已有安装。仅补配 NAS 或客户端升级时加 `--use-existing-skill`，继续指定原 Skill 和本地副本。首次空 NAS 需同时提供 `--remote-root '/Volumes/HR-Hub' --initialize-remote`；已有 NAS 不加初始化开关。临时离线加 `--skip-sync`。

脚本输出 JSON 回执，必须 `ok:true`；从源码安装时，回执的 `dmg` 是已下载路径，`need_install:true` 表示尚需将应用安装到“应用程序”，不能把下载成功当成已安装。配置和回执位于：

```text
~/Library/Application Support/HRResume/client-config.json
~/Library/Application Support/HRResume/setup-result.json
```

安装目录的 `workspace.local.json` 记录解释器、脚本、本地副本和 NAS 的绝对路径。WorkBuddy 导入包路径由回执的 `workbuddy_package` 给出；上传步骤见 [WorkBuddy 导入](agent-install.md#导入-workbuddy)。这是本人电脑的配置包，不能互传。

## 验收与日常使用

用回执中 Skill 目录的 `.venv/bin/python` 调用其中 `scripts/hr.py doctor` 和 `snapshot`，确认 `ok:true`、`healthy:true`。再打开 `招聘看板.app`，核对实际工作副本。首次配置完成后，可以将 `.app` 拷贝到“应用程序”；个人配置始终从上面的用户目录读取，不能移动已安装 Skill 或业务副本。

客户端默认独立窗口；关闭窗口会结束本次服务。Finder 负责再次打开应用时的前台行为，命令行第二实例只复用已验证的服务，不保证将旧窗口置前。`--headless` 仅供测试；无需把本机 HTTP 地址交给同事。

真实 WorkBuddy 导入、公司 NAS 权限及 Mac 实屏使用仍应在目标电脑验收。CI 的编译与无界面检查不能替代这些现场步骤。
