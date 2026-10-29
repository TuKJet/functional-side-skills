# Rust 原生招聘看板

Rust 服务在本机启动，Windows 窗口通过 WebView2、macOS 窗口通过系统 WebKit 内嵌招聘看板。日常 Web UI 没有资料录入表单或技术设置页；维护和审核由 WorkBuddy Skill 完成。

Windows 构建和运行说明如下。macOS 安装见 [Mac 指南](../docs/macos-install.md)；打包命令为 `bash scripts/package-macos.sh aarch64-apple-darwin OUTPUT WHEELHOUSE`（从工程根执行）；仅支持 Apple 芯片 Mac。`.app` 仅包含程序，用户配置和 WebView 缓存位于 `~/Library/Application Support/HRResume/`；源 Skill 安装到用户选择的独立目录。GitHub 自动发布见 [发布说明](../docs/client-releases.md)。

## 构建与启动（给安装 Agent）

```powershell
cargo fmt --check
cargo check
cargo test
cargo build
```

完成后从工程根运行 `scripts/start-web.ps1`，或由同事双击 `启动招聘工作台.cmd`。安装 Agent 首次指定 `-Root` 本地工作副本、`-RemoteRoot` NAS 共享 Hub、`-Actor`、`-Python` 和可选 `-Script`。配置保存到本机 `.runtime/client-config.json`，后续双击沿用。

可直接运行程序并使用 `--config`、`--python`、`--script`、`--root`、`--remote-root`、`--actor`、`--port`、`--headless`。端口默认随机且只监听 `127.0.0.1`；标准输出会打印内部地址供健康检查。正常模式创建原生桌面窗口并在其中嵌入 WebView2，不调用系统浏览器；`--headless` 仅供集成测试和启动脚本测试使用。

普通双击会在项目 `.runtime/client-instance.json` 中记录 loopback URL 和服务 PID，并持有按配置路径命名的 Windows mutex；再次双击只有在 mutex、URL 和 `/api/config` 的 `instance_key` 全部匹配时才复用已有服务并激活已有窗口，避免误连其他本机程序。服务异常退出留下的记录会自动清理。关闭主窗口会结束本次服务；重启电脑后下次启动会清理残留记录。需要立即停止时关闭主窗口即可。

网页资源通过 `include_str!` 内嵌；修改 `web/` 后必须重新 `cargo build` 并重启服务。发布可使用 `cargo build --release`，分发 release 可执行文件与已安装 Skill；运行时不需要旁置 `web/` 源文件。Windows 客户端依赖 WebView2 Runtime，安装 Agent 需检查其是否存在。

## 数据与边界

- WebView2 窗口读取本地快照；点击同步时调用三方合并，将已确认的本地进度与 NAS 交换。同记录冲突不自动覆盖。
- `GET /api/config` 返回连接配置；`POST /api/config` 供安装工具配置，无用户设置页面。
- `POST /api/command` 代理白名单 CLI。简历审核 `review-submit` 被拒绝，审核在 WorkBuddy 中完成。
- POST 要求同源 Origin、正确 loopback Host、JSON 与 `X-HR-Client: 1`；不开放 CORS。
- 文档接口仅允许本地副本内 PDF、Office、文本和常见图片，拒绝 HTML、SVG、可执行文件和越界路径。
- 子进程参数不经过 shell；stdout/stderr 同时读取，超时返回错误。Windows 不弹命令窗口。

## 验证

前端语法检查：`node --check web/app.js`。服务集成测试从工程根执行：

```powershell
$env:HR_WEB_HTTP_TESTS='1'
python -m unittest discover -s tests -p test_web_server.py -v
```

集成测试需要本机 loopback socket 权限，只使用临时合成数据；覆盖静态资源、UTF-8、跨源请求拒绝、文件边界、禁止网页提交审核及大输出管道。

当前 HTTP 库为 [tiny_http 0.12 官方文档](https://docs.rs/tiny_http/0.12.0/tiny_http/)，具体依赖由 Cargo.lock 记录。
