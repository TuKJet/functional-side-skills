# 客户端发布与升级

## 给职能同事

日常只需告诉 WorkBuddy：**“请检查 HR Skill 和招聘客户端是否有新版，保留我的本地资料、NAS 与配置，完成升级后告诉我。”**

安装 Agent 会自动下载客户端并配置好。Windows 是单个 EXE，完成后问你是否创建桌面快捷方式；Mac 是 DMG，安装到“应用程序”。也可从 [最新版本](https://github.com/TuKJet/functional-side-skills/releases/latest) 下载，Mac 仅支持 Apple 芯片。Mac 首次安装见 [Mac 指南](macos-install.md)。

## 给维护 Agent

代码入口：`.github/workflows/hr-client-release.yml`。`main` 中 `简历管理/**` 或该工作流变化会自动构建；其他工程变更不触发 HR 发布。Pull Request 只校验，不发布；也可在 main 上通过 Actions 的 Run workflow 手动构建。

GitHub 在 Windows x64 和 macOS Apple 芯片两台 runner 上运行 Rust/Web/Python 检查，构建内嵌网页的原生客户端、下载依赖 wheelhouse、验证安装和无界面数据接口。所有平台成功后，发布 job 才能创建 Release。普通构建只有仓库只读权限，发布 job 才拥有 `contents:write`。

每次发布保留具体源码提交，tag 使用 `hr-client-v<版本>-run<流水号>`。下载包使用稳定名称，附带标准 SHA256 文件：

- `hr-resume-windows-x64.exe`
- `hr-resume-macos-aarch64.dmg`

客户端版本由 `desktop/Cargo.toml` 管理。修改客户端、打包或安装行为后应检查版本并按需递增；自动流水号使同版本维护构建也可追溯。macOS 暂无开发者签名和公证，不能宣称已签名；后续需配置 Apple Developer 凭据才能加入该步骤。

## 用户升级流程

1. 用已安装的 `hr_update.py check/update` 更新 Skill。依赖变化或本地修改时遵循返回的停止原因，不强行覆盖。
2. 查询仓库最新 HR 客户端 Release，核对 tag 的 `hr-client-` 前缀、平台、源码提交和下载包校验文件；仓库其他工程的 Release 不可作为 HR 包。
3. 使用配置中的解释器运行 `scripts/hr_client.py download --skill-root 已安装Skill目录`。脚本自动选择系统、校验 SHA256、保存新版本文件并写入客户端配置，不覆盖旧版本。Windows 关闭旧客户端后打开返回的 EXE；Mac 挂载返回的 DMG，将新版应用安装到“应用程序”。个人配置位于用户目录，业务副本不移动。
4. 告诉用户客户端入口并询问是否创建/更新桌面快捷方式；用户同意后，Windows 运行 `hr_client.py shortcut --executable 已验证EXE路径`，不同意不创建。该命令验证文件与下载配置、SHA256 一致。
5. 验证 doctor、snapshot、窗口和配置。保留旧客户端目录以便回退。不得覆盖或删除本地工作副本、NAS Hub、简历原件、面试附件和 Excel 备份。按返回路径重新导入 WorkBuddy 包。

Skill 更新器不会替换正在运行的客户端；Agent 按上述步骤处理有客户端变更的发布。只更新 Skill 时不必重装客户端。发布失败时使用上一个成功版本，不能把未通过检查的产物称作正式更新。

参考：[GitHub 发布下载链接](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases)、[GitHub Rust 构建流程](https://docs.github.com/en/actions/tutorials/build-and-test-code/rust)。
