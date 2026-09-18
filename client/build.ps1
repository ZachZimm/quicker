param([string]$PythonPath, [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not (Test-Path .venv-windows\Scripts\python.exe)) {
    if ($PythonPath) { & $PythonPath -m venv .venv-windows }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { py -3.12 -m venv .venv-windows }
    else { throw 'Pass -PythonPath with the full path to a Python 3.12+ installation.' }
    if ($LASTEXITCODE -ne 0) { throw 'Creating the Windows environment failed' }
}
$taskPython = (Resolve-Path .venv-windows\Scripts\python.exe).Path
if (-not $SkipInstall) {
    & $taskPython -m pip install -e '.[client]' pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'Installing build dependencies failed' }
}
# Unrelated DLL directories on PATH can silently supply incompatible Qt/ICU
# dependencies (for example Poppler's icuuc.dll). Build against Windows and the
# selected Python/Qt installation only. Do not change the user's persistent PATH.
$taskOriginalPath = $env:PATH
try {
    $env:PATH = "$(Split-Path $taskPython);$env:SystemRoot\System32;$env:SystemRoot"
    & $taskPython -m PyInstaller --clean --noconfirm --windowed --onedir --name Quicker --distpath client/dist --workpath client/build --specpath client client/launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'Windows packaging failed' }
} finally { $env:PATH = $taskOriginalPath }
Write-Output 'Built client/dist/Quicker/Quicker.exe. Copy the entire Quicker folder to the Windows machine.'
