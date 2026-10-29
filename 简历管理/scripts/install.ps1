[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateNotNullOrEmpty()]
    [string]$Target,
    [Alias('SkipDependencies')]
    [switch]$NoInstall,
    [string]$Wheelhouse = '',
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path (Join-Path $PSScriptRoot '..\skills\manage-hr-resumes')).Path
$destination = [System.IO.Path]::GetFullPath($Target)
if ($destination -eq $source) { throw 'Target must be different from the source Skill directory.' }
if ($destination.StartsWith($source.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Target cannot be inside the source Skill directory.' }
if (Test-Path -LiteralPath $destination) {
    throw "Target already exists; choose a new versioned directory or move it aside first: $destination"
}
if (-not $NoInstall) {
    $pythonCommand = Get-Command $Python -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonPath = $pythonCommand.Source }
    else { throw 'Python was not found. Install Python 3.11+ or pass its full executable path with -Python.' }
    & $pythonPath -c 'import sys,venv,pip; assert sys.version_info >= (3,11), "Python 3.11+ required"; assert tuple(int(x) for x in pip.__version__.split(".")[:2]) >= (22,3), "pip 22.3+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Python、venv 或 pip 前置检查失败；未创建安装目录。' }
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
New-Item -ItemType Directory -Force -Path $destination | Out-Null
foreach ($name in @('SKILL.md', 'requirements.txt', 'skills-upstream.json', 'agents', 'references', 'scripts')) {
    $item = Join-Path $source $name
    if (Test-Path -LiteralPath $item) { Copy-Item -LiteralPath $item -Destination $destination -Recurse -Force }
}
Get-ChildItem -LiteralPath $destination -Recurse -File -Include '*.pyc' | Remove-Item -Force

if (-not $NoInstall) {
    $venv = Join-Path $destination '.venv'
    # --without-pip works on Python distributions that omit ensurepip. Pip's
    # --python target mode below still installs into this private environment.
    & $pythonPath -X utf8 -m venv --without-pip $venv
    if ($LASTEXITCODE -ne 0) { throw "Unable to create private virtual environment (exit $LASTEXITCODE)." }
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) { throw "Private virtual environment is missing its Python executable: $venvPython" }
    # Python distributions without ensurepip are supported through pip's --python target mode.
    if ($Wheelhouse) {
        $wheelPath = (Resolve-Path -LiteralPath $Wheelhouse -ErrorAction Stop).Path
        if (-not (Test-Path -LiteralPath $wheelPath -PathType Container)) { throw "Wheelhouse is not a directory: $Wheelhouse" }
        & $pythonPath -X utf8 -m pip --python $venvPython install --no-index --find-links $wheelPath --disable-pip-version-check -r (Join-Path $destination 'requirements.txt')
    } else {
        & $pythonPath -X utf8 -m pip --python $venvPython install --disable-pip-version-check -r (Join-Path $destination 'requirements.txt')
    }
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed (exit $LASTEXITCODE). Check network/proxy access and retry." }
    & $venvPython -c "import openpyxl, pypdf"
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation reported success but verification imports failed.' }
}
Write-Output "Installed HR resume Skill to $destination"
