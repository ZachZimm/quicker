$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
py -3.12 -m venv .venv-windows
& .\.venv-windows\Scripts\python.exe -m pip install -e '.[client]' pyinstaller
& .\.venv-windows\Scripts\python.exe -m PyInstaller --noconfirm --windowed --onedir --name Quicker --distpath client/dist --workpath client/build --specpath client client/launcher.py
if ($LASTEXITCODE -ne 0) { throw 'Windows packaging failed' }
Write-Output 'Built client/dist/Quicker/Quicker.exe. Copy the entire Quicker folder to the Windows machine.'
