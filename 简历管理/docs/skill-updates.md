# Skill 更新器

此功能只在用户明确授权时运行；没有定时后台检查。安装 Agent 在首次安装完成后建立基线：

```powershell
& $python (Join-Path $skillRoot 'scripts/hr_update.py') init-state --skill-root $skillRoot
```

`$skillRoot` 是实际安装的 `manage-hr-resumes` 目录，`$python` 使用其中 `.venv\Scripts\python.exe`。之后：

```powershell
& $python (Join-Path $skillRoot 'scripts/hr_update.py') check --skill-root $skillRoot
& $python (Join-Path $skillRoot 'scripts/hr_update.py') update --skill-root $skillRoot
& $python (Join-Path $skillRoot 'scripts/hr_update.py') rollback --skill-root $skillRoot
& $python (Join-Path $skillRoot 'scripts/hr_update.py') refresh-package --skill-root $skillRoot
```

每次命令输出单行 JSON；成功为 `ok:true`，失败为 `ok:false` 且退出码非零。更新结果包含 `commit`、`changed_files`、`backup`。上游来源固定为 `TuKJet/functional-side-skills` 的 `简历管理/skills/manage-hr-resumes`、`main` 分支；脚本使用 GitHub API 找到最新路径提交，再按完整 SHA 下载归档。没有真实上游路径时返回 `upstream_missing`，不会假称已发布。

更新器维护 `.update/manifest.json`、独占锁、临时 stage 和备份；只替换 Skill 托管文件。它保留 `workspace.local.json`、`.venv`、本地简历池、NAS、Excel、Rust 看板 EXE 和其他业务文件。若检测到本地托管文件修改，返回 `local_changes`；若 `requirements.txt` 变化，返回 `dependency_change` 并停止，不安装系统依赖或进行半升级。损坏、越界、带符号链接或超限 ZIP 也不会改动安装。

成功更新或回退后会生成 `.update/workbuddy-package.zip`；首次安装后若包丢失，也可单独运行 `refresh-package`。把该包重新导入 WorkBuddy，或在 WorkBuddy 开新会话并重新导入，才能刷新 `SKILL.md` 指令。回退仅接受当前 Skill `.update/backups` 内且完整哈希校验通过的备份；若当前托管文件已有改动会返回 `local_changes`。它同样不触碰业务工作副本和看板程序。

### 中断恢复

若 `.update/transaction.json` 存在，先不要运行 `hr.py` 业务命令、`check` 或 `update`；它表示更新/回退可能在文件替换中断。JSON 中的 `backup` 是已验证备份目录。优先使用当前安装的 Python 执行：

```powershell
& $python (Join-Path $skillRoot 'scripts/hr_update.py') rollback --skill-root $skillRoot --backup $backup
```

如果当前 `scripts/hr_update.py` 已缺失，不能把 `.py` 当作可执行程序；使用安装目录 `.venv\Scripts\python.exe` 调用备份中的脚本：

```powershell
& $python (Join-Path $backup 'scripts/hr_update.py') rollback --skill-root $skillRoot --backup $backup
```

回退成功后，用 `doctor`/`check` 验证 manifest 和 Skill 文件，再运行 `refresh-package` 重新导入 WorkBuddy。回退失败时保留 `transaction.json`，修复锁定或权限问题后重试，不要删除 journal、lock 或备份目录。
