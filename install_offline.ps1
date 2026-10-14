param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not $PythonPath) {
    $projectPython = Join-Path $PSScriptRoot '.runtime/python/python.exe'
    if (Test-Path -LiteralPath $projectPython) { $PythonPath = $projectPython }
    else { $PythonPath = 'python' }
}
& $PythonPath -c "import sys,sysconfig; assert sys.version_info[:2] == (3,12) and sysconfig.get_platform() == 'win-amd64', 'Use official 64-bit CPython 3.12, not Inkscape Python'"
if ($LASTEXITCODE -ne 0) { throw 'A compatible CPython 3.12 runtime is required. Pass -PythonPath to its python.exe.' }
& $PythonPath -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    & $PythonPath -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'Unable to bootstrap pip in the selected Python runtime.' }
}
& $PythonPath -m pip install --only-binary=:all: --target vendor/offline --upgrade -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Offline dependencies could not be installed.' }
& $PythonPath tools/install_offline_model.py
if ($LASTEXITCODE -ne 0) { throw 'Model download or verification failed.' }
$selectedRuntime = (& $PythonPath -X utf8 -c 'import sys; print(sys.executable)').Trim()
if (-not (Test-Path -LiteralPath $selectedRuntime -PathType Leaf)) { throw 'Selected Python runtime path is invalid.' }
$translatorRoot = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
if ($selectedRuntime.StartsWith($translatorRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    $selectedRuntime = $selectedRuntime.Substring($translatorRoot.Length).Replace('\', '/')
}
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'config') | Out-Null
Set-Content -LiteralPath (Join-Path $PSScriptRoot 'config/python_runtime.txt') -Value $selectedRuntime -Encoding Unicode
Write-Host 'Ready. Open launch_app.vbs. Translation now runs entirely offline.'
