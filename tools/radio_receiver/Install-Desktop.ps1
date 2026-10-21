param([string]$ShortcutPath, [string]$ConfigDirectory, [string]$PythonExecutable)
$ErrorActionPreference='Stop'
if ($ConfigDirectory) {$env:UAV_RADIO_CONFIG_DIR=$ConfigDirectory}
if ($PythonExecutable) {$env:UAV_RADIO_PYTHON=$PythonExecutable}
. "$PSScriptRoot\RadioRelay-Common.ps1"
if (-not $ShortcutPath) {$ShortcutPath=Join-Path ([Environment]::GetFolderPath('Desktop')) '飞机数据接收.lnk'}
$radioShell=New-Object -ComObject WScript.Shell
$radioShortcut=$radioShell.CreateShortcut([System.IO.Path]::GetFullPath($ShortcutPath))
$radioShortcut.TargetPath="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$radioShortcut.Arguments='-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File '+(Quote-RadioPath "$radioRoot\RadioRelay-Control.ps1")+' -ConfigDirectory '+(Quote-RadioPath $radioState)+' -PythonExecutable '+(Quote-RadioPath $radioPython)
$radioShortcut.WorkingDirectory=$radioRoot
$radioShortcut.Description='接收飞机测量数据；关闭窗口退出全部本机进程'
$radioShortcut.IconLocation="$env:SystemRoot\System32\shell32.dll,17"
$radioShortcut.WindowStyle=7
$radioShortcut.Save()
Write-Output "Desktop shortcut created: $ShortcutPath"
