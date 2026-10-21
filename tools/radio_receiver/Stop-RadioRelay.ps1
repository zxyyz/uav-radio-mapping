param([int]$ExpectedClientPid=0, [string]$Reason="manual")
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\RadioRelay-Common.ps1"
$radioLock = [System.Threading.Mutex]::new($false, 'Local\UavRadioRelayService')
$radioLocked = $false
try {
    try { $radioLocked=$radioLock.WaitOne(15000) } catch [System.Threading.AbandonedMutexException] { $radioLocked=$true }
    if (-not $radioLocked) { throw 'Another relay control operation is running.' }
    if ($ExpectedClientPid -gt 0) {
        $radioSaved = Get-Content -LiteralPath "$radioState\relay-processes.private.json" -Raw | ConvertFrom-Json
        if ($radioSaved.relay_client_pid -ne $ExpectedClientPid) {Write-Output 'Stale auto-stop ignored.'; return}
    }
    Set-RadioEnabled $false $Reason
    foreach ($radioScript in @('relay_client.py','server.py')) {
        foreach ($radioWorker in @(Get-RadioManagedWorkers | Where-Object Script -eq $radioScript)) {
            Stop-Process -Id $radioWorker.ProcessId -ErrorAction SilentlyContinue
            Wait-Process -Id $radioWorker.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
        }
    }
    @{} | ConvertTo-Json | Set-Content -LiteralPath "$radioState\relay-processes.private.json" -Encoding utf8
    @{} | ConvertTo-Json | Set-Content -LiteralPath "$radioState\processes.private.json" -Encoding utf8
    Write-Output 'Local reception stopped; server queue retained.'
} finally {
    if ($radioLocked) { $radioLock.ReleaseMutex() }
    $radioLock.Dispose()
}
