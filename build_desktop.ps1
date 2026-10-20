param([string]$Python = 'python', [string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$componentPython = (Get-Command $Python -ErrorAction Stop).Source
$componentOutput = Join-Path $env:LOCALAPPDATA 'THSTeacherDesktop/app'
if ($OutputDirectory) { $componentOutput = [IO.Path]::GetFullPath($OutputDirectory) }
$componentBuild = Join-Path $env:LOCALAPPDATA 'THSTeacherDesktop/build'
& $componentPython -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name THSTeacher --distpath $componentOutput --workpath $componentBuild `
    --specpath $componentBuild --paths $PSScriptRoot `
    --add-data ((Join-Path $PSScriptRoot 'web') + ';web') `
    --add-data ((Join-Path $PSScriptRoot 'desktop') + ';desktop') `
    --hidden-import webview.platforms.edgechromium `
    --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2 --exclude-module PySide6 `
    --exclude-module numpy --exclude-module pandas --exclude-module matplotlib --exclude-module IPython `
    --exclude-module pytest --exclude-module tkinter --exclude-module notebook `
    (Join-Path $PSScriptRoot 'desktop_app.py')
if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed' }
Write-Output (Join-Path $componentOutput 'THSTeacher/THSTeacher.exe')
