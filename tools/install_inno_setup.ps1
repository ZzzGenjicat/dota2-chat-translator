$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskTools = Join-Path $taskRoot '.tools'
$taskInnoDirectory = Join-Path $taskTools 'inno-6.7.3'
$taskInnoCompiler = Join-Path $taskInnoDirectory 'ISCC.exe'
if (Test-Path -LiteralPath $taskInnoCompiler) {
    Write-Output $taskInnoCompiler
    exit 0
}
New-Item -ItemType Directory -Path $taskTools -Force | Out-Null
$taskDownload = Join-Path $taskTools 'innosetup-6.7.3.exe'
$taskUrl = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
$taskSha256 = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
Invoke-WebRequest -UseBasicParsing -Uri $taskUrl -OutFile $taskDownload -TimeoutSec 120
if ((Get-FileHash -LiteralPath $taskDownload -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskSha256) {
    throw 'Official Inno Setup installer SHA256 did not match the pinned release'
}
$taskArguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', ('/DIR="' + $taskInnoDirectory + '"'))
$taskProcess = Start-Process -FilePath $taskDownload -ArgumentList $taskArguments -WindowStyle Hidden -Wait -PassThru
if ($taskProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $taskInnoCompiler)) {
    throw 'Inno Setup compiler installation failed'
}
Write-Output $taskInnoCompiler
