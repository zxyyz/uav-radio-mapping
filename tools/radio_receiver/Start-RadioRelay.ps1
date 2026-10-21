param([switch]$Manual)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\RadioRelay-Common.ps1"
$radioManifest = @{}
function Start-RadioWorker([string]$radioScript, [string]$radioConfig, [string]$radioLogName) {
    $radioExisting = Get-RadioManagedWorkers | Where-Object Script -eq $radioScript | Select-Object -First 1
    if ($radioExisting) { return [int]$radioExisting.ProcessId }
    $radioProcess = Start-Process -FilePath $radioPython -ArgumentList @('-X','utf8',(Quote-RadioPath "$radioRoot\$radioScript"),'--config',(Quote-RadioPath "$radioState\$radioConfig")) -WorkingDirectory $radioRoot -WindowStyle Hidden -RedirectStandardOutput "$radioState\$radioLogName.log" -RedirectStandardError "$radioState\$radioLogName-errors.log" -PassThru
    return [int]$radioProcess.Id
}
$radioLock = [System.Threading.Mutex]::new($false, 'Local\UavRadioRelayService')
$radioLocked = $false
try {
    try { $radioLocked = $radioLock.WaitOne(15000) } catch [System.Threading.AbandonedMutexException] { $radioLocked = $true }
    if (-not $radioLocked) { throw 'Another relay control operation is running.' }
    if ($Manual) { Set-RadioEnabled $true }
    if (-not (Get-RadioEnabled)) { Write-Output 'Reception disabled by the desktop control.'; exit }
    $radioManifest.receiver_pid = Start-RadioWorker 'server.py' 'receiver.private.json' 'receiver'
    $radioManifest.relay_client_pid = Start-RadioWorker 'relay_client.py' 'relay-client.private.json' 'relay-client'
    $radioManifest | ConvertTo-Json | Set-Content -LiteralPath "$radioState\relay-processes.private.json" -Encoding utf8
    $radioManifest | ConvertTo-Json | Set-Content -LiteralPath "$radioState\processes.private.json" -Encoding utf8
    Write-Output "Receiver PID: $($radioManifest.receiver_pid); relay client PID: $($radioManifest.relay_client_pid)"
} finally {
    if ($radioLocked) { $radioLock.ReleaseMutex() }
    $radioLock.Dispose()
}
