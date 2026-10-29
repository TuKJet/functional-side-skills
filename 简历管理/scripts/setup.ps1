#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SkillTarget,
    [Parameter(Mandatory)][string]$LocalRoot,
    [Parameter(Mandatory)][string]$Actor,
    [string]$RemoteRoot = '',
    [string]$Python = 'python',
    [string]$Wheelhouse = '',
    [switch]$InitializeRemote,
    [switch]$UseExistingSkill,
    [switch]$SkipSync,
    [switch]$SkipClientDownload
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$skillRoot = [IO.Path]::GetFullPath($SkillTarget)
$localPath = [IO.Path]::GetFullPath($LocalRoot)
if (-not $Actor.Trim()) { throw 'Actor 必须是这台电脑实际操作人的姓名。' }
if ($localPath.StartsWith('\\')) { throw 'LocalRoot 必须在本机磁盘，不能直接指向 NAS UNC 路径。' }
if ($InitializeRemote -and -not $RemoteRoot) { throw 'InitializeRemote 必须同时指定 RemoteRoot。' }
if ($RemoteRoot) {
    $remotePath = [IO.Path]::GetFullPath($RemoteRoot)
    if ($remotePath -eq $localPath) { throw 'LocalRoot 与 RemoteRoot 不能相同。' }
} else { $remotePath = '' }
if (-not $Wheelhouse -and (Test-Path -LiteralPath (Join-Path $projectRoot 'wheelhouse'))) {
    $Wheelhouse = Join-Path $projectRoot 'wheelhouse'
}
$packageExe = Join-Path $projectRoot '招聘看板.exe'
function Assert-ClientRuntime([string]$ClientExecutable) {
    # Windows GUI executables do not reliably wait in PowerShell's native
    # pipeline. Own the redirected process until its diagnostic output drains.
    $runtimeStart = [Diagnostics.ProcessStartInfo]::new()
    $runtimeStart.FileName = $ClientExecutable
    $runtimeStart.ArgumentList.Add('--check-runtime')
    $runtimeStart.UseShellExecute = $false
    $runtimeStart.CreateNoWindow = $true
    $runtimeStart.RedirectStandardOutput = $true
    $runtimeStart.RedirectStandardError = $true
    $runtimeStart.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $runtimeStart.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $runtimeProcess = [Diagnostics.Process]::Start($runtimeStart)
    try {
        $runtimeOutput = $runtimeProcess.StandardOutput.ReadToEndAsync()
        $runtimeErrors = $runtimeProcess.StandardError.ReadToEndAsync()
        if (-not $runtimeProcess.WaitForExit(15000)) {
            $runtimeProcess.Kill()
            $runtimeProcess.WaitForExit()
            throw 'WebView2 Runtime 检查超时。请让安装 Agent 检查运行环境。'
        }
        $runtimeCheck = $runtimeOutput.GetAwaiter().GetResult()
        $runtimeError = $runtimeErrors.GetAwaiter().GetResult()
        if ($runtimeProcess.ExitCode -ne 0) { throw "WebView2 Runtime 未就绪，请按安装指南补齐：$runtimeCheck $runtimeError" }
    } finally { $runtimeProcess.Dispose() }
    try { $webviewStatus = $runtimeCheck | ConvertFrom-Json }
    catch { throw '客户端版本不兼容；请使用本次桌面客户端发布包。' }
    if (-not $webviewStatus.ok) { throw "WebView2 Runtime 检查失败：$($webviewStatus.error)" }
}
if (Test-Path -LiteralPath $packageExe) { Assert-ClientRuntime $packageExe }

if ($UseExistingSkill) {
    if (-not (Test-Path -LiteralPath (Join-Path $skillRoot 'SKILL.md')) -or
        -not (Test-Path -LiteralPath (Join-Path $skillRoot 'scripts\hr_resume\service.py'))) {
        throw 'UseExistingSkill 目标不是已安装的 HR Skill；不改动它。'
    }
} else {
    & (Join-Path $PSScriptRoot 'install.ps1') -Target $skillRoot -Python $Python -Wheelhouse $Wheelhouse
}
$venvPython = Join-Path $skillRoot '.venv\Scripts\python.exe'
$entry = Join-Path $skillRoot 'scripts\hr.py'
if (-not (Test-Path -LiteralPath $venvPython)) { throw '未找到 Skill 私有 Python。请完成依赖安装后再配置。' }
& $venvPython -c 'import openpyxl,pypdf'
if ($LASTEXITCODE -ne 0) { throw 'Skill 依赖不完整，请修复安装。' }

function Invoke-HrCommand([string]$Command, [hashtable]$Payload = @{}) {
    $json = $Payload | ConvertTo-Json -Depth 20 -Compress
    $resultText = & $venvPython $entry --root $localPath --actor $Actor $Command --payload $json
    $exitCode = $LASTEXITCODE
    try { $result = ($resultText -join "`n") | ConvertFrom-Json }
    catch { throw "命令 $Command 没有返回有效 JSON：$resultText" }
    if ($exitCode -ne 0 -or -not $result.ok) { throw "命令 $Command 失败：$($result.error)" }
    return $result.data
}

# init accepts an existing HR pool but rejects a nonempty unrelated folder.
$null = Invoke-HrCommand 'init'
$health = Invoke-HrCommand 'doctor'
if (-not $health.healthy) { throw "工作副本检查失败：$($health.issues -join '；')" }

$runtime = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$configPath = Join-Path $runtime 'client-config.json'
$config = [ordered]@{
    python=$venvPython; script=$entry; root=$localPath; actor=$Actor;
    remote_root=$remotePath; port=0; config_path=$configPath
}
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json), $utf8)
# This file belongs to the installed copy and supplies all per-user defaults to the Agent.
$workspaceFile = Join-Path $skillRoot 'workspace.local.json'
[IO.File]::WriteAllText($workspaceFile, ($config | ConvertTo-Json), $utf8)
& $venvPython (Join-Path $skillRoot 'scripts\hr_update.py') init-state --skill-root $skillRoot
if ($LASTEXITCODE -ne 0) { throw 'Skill 更新基线检查失败；不覆盖已有本地修改。' }

# Import only the small Skill plus its per-machine pointers into WorkBuddy.
# Python stays in the stable installation directory, never inside the upload.
$importZip = Join-Path $runtime 'manage-hr-resumes-workbuddy.zip'
$tempZip = Join-Path $runtime ('workbuddy-' + [guid]::NewGuid().ToString('N') + '.zip')
$archive = [IO.Compression.ZipFile]::Open($tempZip, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($item in Get-ChildItem -LiteralPath $skillRoot -Recurse -File) {
        $relative = [IO.Path]::GetRelativePath($skillRoot, $item.FullName)
        if ($relative -match '(^|[\\/])(__pycache__|\.venv|\.update)([\\/]|$)' -or $relative -match '\.pyc$') { continue }
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $item.FullName, $relative.Replace('\','/')) | Out-Null
    }
} finally { $archive.Dispose() }
Move-Item -LiteralPath $tempZip -Destination $importZip -Force

$syncResult = 'offline'
if ($remotePath -and -not $SkipSync) {
    $synced = Invoke-HrCommand 'sync' @{remote_root=$remotePath;initialize_remote=[bool]$InitializeRemote}
    if (-not $synced.synced) { throw '已保存本地配置，但存在同步冲突。请让 Agent 比较双方记录；没有覆盖数据。' }
    $syncResult = 'synced'
}
$exe = Join-Path $projectRoot '招聘看板.exe'
if (-not (Test-Path -LiteralPath $exe)) { $exe = Join-Path $projectRoot 'desktop\target\release\hr-resume-viewer.exe' }
$clientReady = Test-Path -LiteralPath $exe
if (-not $clientReady -and -not $SkipClientDownload) {
    $downloadText = & $venvPython (Join-Path $skillRoot 'scripts\hr_client.py') download --skill-root $skillRoot
    if ($LASTEXITCODE -ne 0) { throw "Skill 已安装，但客户端下载失败；请让 Agent 重试：$downloadText" }
    $download = ($downloadText -join "`n") | ConvertFrom-Json
    if (-not $download.ok -or -not $download.executable) { throw '客户端下载结果无效。' }
    $exe = $download.executable
    Assert-ClientRuntime $exe
    $configPath = Join-Path $env:LOCALAPPDATA 'HRResume\client-config.json'
    $clientReady = $true
}
$receipt = [ordered]@{
    ok=$true; skill=$skillRoot; workspace_config=$workspaceFile; web_config=$configPath; workbuddy_package=$importZip;
    local_root=$localPath; remote_root=$remotePath; sync=$syncResult; executable=$exe;
    client_ready=[bool]$clientReady; shortcut_created=$false;
    next='导入 workbuddy_package 后告知客户端入口；询问用户是否创建桌面快捷方式，只有明确同意后才调用 hr_client.py shortcut。'
}
$receiptFile = Join-Path $runtime 'setup-result.json'
$receipt.receipt = $receiptFile
$resultJson = $receipt | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText($receiptFile, $resultJson, $utf8)
Write-Output $resultJson
