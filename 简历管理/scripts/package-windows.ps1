#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$OutputDirectory = '',
    [string]$Wheelhouse = '',
    [string]$Executable = '',
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot ('dist\hr-resume-windows-x64-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$destination = [IO.Path]::GetFullPath($OutputDirectory)
if ((Test-Path -LiteralPath $destination) -or (Test-Path -LiteralPath ($destination + '.zip'))) { throw '分发目标已存在，请换一个输出目录；不会覆盖。' }
if (-not $SkipBuild) {
    & cargo build --release --manifest-path (Join-Path $projectRoot 'desktop\Cargo.toml')
    if ($LASTEXITCODE -ne 0) { throw 'Rust release 构建失败。' }
}
$releaseExe = if ($Executable) { (Resolve-Path -LiteralPath $Executable).Path } else { Join-Path $projectRoot 'desktop\target\release\hr-resume-viewer.exe' }
if (-not (Test-Path -LiteralPath $releaseExe)) { throw '未找到 release exe。' }
New-Item -ItemType Directory -Force -Path $destination | Out-Null
Copy-Item -LiteralPath $releaseExe -Destination (Join-Path $destination '招聘看板.exe')
Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination $destination
Copy-Item -LiteralPath (Join-Path $projectRoot '使用说明.html') -Destination $destination
Copy-Item -LiteralPath (Join-Path $projectRoot 'docs') -Destination $destination -Recurse
New-Item -ItemType Directory -Force -Path (Join-Path $destination 'scripts') | Out-Null
foreach ($name in @('install.ps1','setup.ps1','start-web.ps1','create_demo.py')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $destination 'scripts')
}
$sourceSkill = Join-Path $projectRoot 'skills\manage-hr-resumes'
$packSkill = Join-Path $destination 'skills\manage-hr-resumes'
New-Item -ItemType Directory -Force -Path $packSkill | Out-Null
# Copy explicit distributable content, never local configuration or caches.
foreach ($item in Get-ChildItem -LiteralPath $sourceSkill -Recurse -File) {
    $relative = [IO.Path]::GetRelativePath($sourceSkill, $item.FullName)
    if ($relative -match '(^|[\\/])(__pycache__|\.venv|\.update)([\\/]|$)' -or
        $relative -match '\.pyc$|(^|[\\/])workspace\.local\.json$') { continue }
    $target = Join-Path $packSkill $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $item.FullName -Destination $target
}
if ($Wheelhouse) {
    $wheels = (Resolve-Path -LiteralPath $Wheelhouse).Path
    New-Item -ItemType Directory -Force -Path (Join-Path $destination 'wheelhouse') | Out-Null
    Get-ChildItem -LiteralPath $wheels -Filter '*.whl' -File | Copy-Item -Destination (Join-Path $destination 'wheelhouse')
}
Compress-Archive -LiteralPath $destination -DestinationPath ($destination + '.zip') -CompressionLevel Optimal
[ordered]@{directory=$destination;zip=$destination+'.zip';exe_sha256=(Get-FileHash -LiteralPath (Join-Path $destination '招聘看板.exe') -Algorithm SHA256).Hash} | ConvertTo-Json
