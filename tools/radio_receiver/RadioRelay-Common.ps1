$radioRoot = $PSScriptRoot
$radioRepo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$radioState = if ($env:UAV_RADIO_CONFIG_DIR) {[System.IO.Path]::GetFullPath($env:UAV_RADIO_CONFIG_DIR)} else {Join-Path $radioRepo 'local\radio_receiver'}
$env:UAV_RADIO_CONFIG_DIR = $radioState
New-Item -ItemType Directory -Path $radioState -Force | Out-Null
$radioPython = if ($env:UAV_RADIO_PYTHON) {$env:UAV_RADIO_PYTHON} elseif (Test-Path -LiteralPath "$radioRepo\.venv\Scripts\python.exe") {"$radioRepo\.venv\Scripts\python.exe"} else {(Get-Command python.exe -ErrorAction Stop).Source}
$radioPreferencePath = Join-Path $radioState 'control.private.json'
function Quote-RadioPath([string]$radioPath) {return ('"{0}"' -f $radioPath)}
function Get-RadioManagedWorkers {
    $radioCandidates = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'"
    foreach ($radioCandidate in $radioCandidates) {
        foreach ($radioPair in @(@('server.py','receiver.private.json'),@('relay_client.py','relay-client.private.json'))) {
            if ($radioCandidate.CommandLine -like "*$radioRoot\$($radioPair[0])*" -and $radioCandidate.CommandLine -like "*$radioState\$($radioPair[1])*" -and $radioCandidate.CommandLine -notlike '*--once*') {
                [PSCustomObject]@{ Script=$radioPair[0]; ProcessId=[int]$radioCandidate.ProcessId }
            }
        }
    }
}
function Set-RadioEnabled([bool]$radioEnabled, [string]$radioReason='') {
    @{ enabled=$radioEnabled; stop_reason=$radioReason } | ConvertTo-Json | Set-Content -LiteralPath $radioPreferencePath -Encoding utf8
}
function Get-RadioStopReason {
    if (-not (Test-Path -LiteralPath $radioPreferencePath)) {return ''}
    return [string]((Get-Content -LiteralPath $radioPreferencePath -Raw | ConvertFrom-Json).stop_reason)
}
function Get-RadioEnabled {
    if (-not (Test-Path -LiteralPath $radioPreferencePath)) { return $true }
    return [bool]((Get-Content -LiteralPath $radioPreferencePath -Raw | ConvertFrom-Json).enabled)
}
