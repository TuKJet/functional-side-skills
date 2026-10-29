[CmdletBinding()]
param(
    [string]$Root = '',
    [string]$Python = '',
    [string]$Script = '',
    [string]$Actor = '',
    [string]$RemoteRoot = '',
    [string]$Config = '',
    [string]$Exe = '',
    [switch]$NoOpen,
    [switch]$Headless
)

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Exe) {
    foreach ($candidate in @('招聘看板.exe', 'desktop\target\release\hr-resume-viewer.exe', 'desktop\target\debug\hr-resume-viewer.exe')) {
        $path = Join-Path $project $candidate
        if (Test-Path -LiteralPath $path -PathType Leaf) { $Exe = $path; break }
    }
}
if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
    throw "Web 工作台程序不存在：$Exe。请让安装 Agent 先在 desktop 目录执行 cargo build。"
}
if (-not $Config) { $Config = Join-Path $project '.runtime\client-config.json' }
$runtime = Join-Path $project '.runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

# Persist only explicitly supplied values. A normal double-click therefore
# reuses the Agent's saved local root, remote root, Python, script and actor.
$saved = [ordered]@{ python='python'; script=(Join-Path $project 'skills\manage-hr-resumes\scripts\hr.py'); root=''; actor=''; remote_root=''; port=0; config_path=$Config }
if (Test-Path -LiteralPath $Config) {
    try {
        $existing = Get-Content -Raw -LiteralPath $Config | ConvertFrom-Json
        foreach ($name in @('python','script','root','actor','remote_root')) {
            if ($null -ne $existing.$name) { $saved[$name] = [string]$existing.$name }
        }
    } catch { throw "无法读取配置文件：$Config；请让安装 Agent 修复配置。" }
}
foreach ($pair in @(@('python',$Python),@('script',$Script),@('root',$Root),@('actor',$Actor),@('remote_root',$RemoteRoot))) {
    if ($pair[1]) { $saved[$pair[0]] = $pair[1] }
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Config) | Out-Null
[IO.File]::WriteAllText($Config, ($saved | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))

$stamp = [guid]::NewGuid().ToString('N')
$outLog = Join-Path $runtime "start-web-$stamp.out.log"
$errLog = Join-Path $runtime "start-web-$stamp.err.log"
$psi = [Diagnostics.ProcessStartInfo]::new()
$psi.FileName = (Resolve-Path -LiteralPath $Exe).Path
$psi.WorkingDirectory = $project
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
$psi.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
if ($Headless -or $NoOpen) { $psi.ArgumentList.Add('--headless') }
$psi.ArgumentList.Add('--config')
$psi.ArgumentList.Add((Resolve-Path -LiteralPath $Config).Path)
$process = [Diagnostics.Process]::Start($psi)
$deadline = (Get-Date).AddSeconds(20)
$url = $null
$readTask = $process.StandardOutput.ReadLineAsync()
while ((Get-Date) -lt $deadline) {
    if ($readTask.IsCompleted) {
        $line = $readTask.GetAwaiter().GetResult()
        if ($line) { Add-Content -LiteralPath $outLog -Value $line; $m = [regex]::Match($line, 'http://127\.0\.0\.1:\d+/'); if ($m.Success) { $url = $m.Value; break } }
        if ($process.HasExited) { break }
        $readTask = $process.StandardOutput.ReadLineAsync()
    } elseif ($process.HasExited) {
        # A duplicate normal instance exits after printing its reusable URL.
        # Give the redirected stdout reader a chance to drain that line.
        Start-Sleep -Milliseconds 25
        continue
    }
    Start-Sleep -Milliseconds 100
}
if (-not $url) {
    if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit(3000) | Out-Null }
    $err = if ($process.HasExited) { $process.StandardError.ReadToEnd() } else { '' }
    throw "Web 工作台未从 stdout 报告 URL。进程退出=$($process.HasExited)。$err"
}
$handler = [Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(2)
$ready = $false
try {
    $readyDeadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $readyDeadline) {
        try { $response = $client.GetAsync($url).GetAwaiter().GetResult(); $status = [int]$response.StatusCode; $response.Dispose(); if ($status -eq 200) { $ready = $true; break } } catch {}
        Start-Sleep -Milliseconds 100
    }
} finally { $client.Dispose() }
if (-not $ready) { if (-not $process.HasExited) { $process.Kill() }; throw "Web 工作台 URL 已报告但 HTTP 尚未就绪：$url" }
$servicePid = $process.Id
if (-not ($Headless -or $NoOpen)) {
    $instancePath = Join-Path (Split-Path -Parent $Config) 'client-instance.json'
    $instanceDeadline = (Get-Date).AddSeconds(2)
    while ((Get-Date) -lt $instanceDeadline -and -not (Test-Path -LiteralPath $instancePath -PathType Leaf)) {
        Start-Sleep -Milliseconds 50
    }
    if (-not (Test-Path -LiteralPath $instancePath -PathType Leaf)) {
        throw "普通模式未找到实例元数据：$instancePath"
    }
    try {
        $instance = Get-Content -Raw -LiteralPath $instancePath | ConvertFrom-Json
        if (-not $instance.pid) { throw "缺少服务 pid" }
        $servicePid = [int]$instance.pid
    } catch { throw "无法读取实例元数据：$instancePath；$($_.Exception.Message)" }
}
[IO.File]::WriteAllText((Join-Path $runtime 'last-web-process.json'), (@{pid=$servicePid;exe=$psi.FileName;url=$url;config=$Config;headless=($Headless -or $NoOpen)} | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
Write-Output $url
