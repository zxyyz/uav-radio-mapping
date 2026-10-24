param([switch]$Preview, [switch]$VerifyControls, [switch]$VerifyClose, [switch]$VerifyCloseDuringStart, [string]$ConfigDirectory, [string]$PythonExecutable)
$ErrorActionPreference = 'Stop'
if ($ConfigDirectory) {$env:UAV_RADIO_CONFIG_DIR=$ConfigDirectory}
if ($PythonExecutable) {$env:UAV_RADIO_PYTHON=$PythonExecutable}
. "$PSScriptRoot\RadioRelay-Common.ps1"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$radioCreated=$false
$radioWindowLock=[System.Threading.Mutex]::new($true,'Local\UavRadioRelayControl',[ref]$radioCreated)
if (-not $radioCreated -and -not $Preview) {
    [System.Windows.Forms.MessageBox]::Show('控制窗口已经打开，请在任务栏中找到它。','飞机数据接收') | Out-Null
    $radioWindowLock.Dispose();exit
}
$form=[System.Windows.Forms.Form]::new()
$form.Text='飞机数据接收'
$form.ClientSize=[System.Drawing.Size]::new(560,470)
$form.StartPosition='CenterScreen'
$form.FormBorderStyle='FixedSingle'
$form.MaximizeBox=$false
$form.BackColor=[System.Drawing.ColorTranslator]::FromHtml('#F5F7FA')
$form.Font=[System.Drawing.Font]::new('Microsoft YaHei UI',10)
$form.AutoScaleMode='Dpi'
$title=[System.Windows.Forms.Label]::new()
$title.Text='飞机数据接收';$title.Font=[System.Drawing.Font]::new('Microsoft YaHei UI',20,[System.Drawing.FontStyle]::Bold)
$title.Location=[System.Drawing.Point]::new(28,22);$title.Size=[System.Drawing.Size]::new(490,45)
$form.Controls.Add($title)
$route=[System.Windows.Forms.Label]::new()
$route.Text='飞机 → 服务器 → 本机数据库'
$route.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#667085')
$route.Location=[System.Drawing.Point]::new(30,76);$route.Size=[System.Drawing.Size]::new(490,28)
$form.Controls.Add($route)
$serverHeading=[System.Windows.Forms.Label]::new()
$serverHeading.Text='服务器待同步到本机';$serverHeading.Location=[System.Drawing.Point]::new(30,120)
$serverHeading.Size=[System.Drawing.Size]::new(490,28)
$form.Controls.Add($serverHeading)
$serverPending=[System.Windows.Forms.Label]::new()
$serverPending.Text='正在查询…';$serverPending.Location=[System.Drawing.Point]::new(28,152)
$serverPending.Size=[System.Drawing.Size]::new(500,45)
$serverPending.Font=[System.Drawing.Font]::new('Microsoft YaHei UI',23,[System.Drawing.FontStyle]::Bold)
$serverPending.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#185ABC')
$form.Controls.Add($serverPending)
$serverDetail=[System.Windows.Forms.Label]::new()
$serverDetail.Text='尚未取得服务器状态';$serverDetail.Location=[System.Drawing.Point]::new(30,205)
$serverDetail.Size=[System.Drawing.Size]::new(500,28)
$serverDetail.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#667085')
$form.Controls.Add($serverDetail)
$status=[System.Windows.Forms.Label]::new()
$status.Font=[System.Drawing.Font]::new('Microsoft YaHei UI',13,[System.Drawing.FontStyle]::Bold)
$status.Location=[System.Drawing.Point]::new(30,252);$status.Size=[System.Drawing.Size]::new(490,30)
$form.Controls.Add($status)
$toggle=[System.Windows.Forms.Button]::new()
$toggle.Location=[System.Drawing.Point]::new(30,297);$toggle.Size=[System.Drawing.Size]::new(220,52)
$toggle.FlatStyle='Flat';$toggle.FlatAppearance.BorderSize=0
$toggle.Font=[System.Drawing.Font]::new('Microsoft YaHei UI',12,[System.Drawing.FontStyle]::Bold)
$toggle.ForeColor=[System.Drawing.Color]::White
$toggle.Cursor=[System.Windows.Forms.Cursors]::Hand
$form.Controls.Add($toggle)
$note=[System.Windows.Forms.Label]::new()
$note.Text="连续 10 秒无待同步数据后，自动停止接收。`n关闭窗口退出所有本机进程；长期保存模式不删服务器数据。"
$note.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#667085')
$note.Location=[System.Drawing.Point]::new(30,375);$note.Size=[System.Drawing.Size]::new(500,65)
$form.Controls.Add($note)
$script:radioOperation=$null
$script:radioIsRunning=$false
$script:radioMonitor=$null
$script:radioNextMonitor=[DateTime]::MinValue
$script:radioResourcesClosed=$false
function Close-RadioWindowResources {
    if ($script:radioResourcesClosed) {return}
    $timer.Stop()
    $toggle.Enabled=$false
    foreach ($radioChildName in @('radioMonitor','radioOperation')) {
        $radioChild=Get-Variable -Name $radioChildName -Scope Script -ValueOnly
        if ($radioChild) {
            try {
                if (-not $radioChild.HasExited) {$radioChild.Kill();$radioChild.WaitForExit(5000) | Out-Null}
            } catch {} finally {
                $radioChild.Dispose()
                Set-Variable -Name $radioChildName -Scope Script -Value $null
            }
        }
    }
    if (-not $Preview) {
        try {
            & "$radioRoot\Stop-RadioRelay.ps1" -Reason window_closed | Out-Null
        } catch {
            Set-RadioEnabled $false 'window_closed'
            foreach ($radioWorker in @(Get-RadioManagedWorkers)) {
                Stop-Process -Id $radioWorker.ProcessId -ErrorAction SilentlyContinue
                Wait-Process -Id $radioWorker.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
            }
        }
        # Retire an auto-stop helper that may have been launched just before exit.
        $radioHelpers=Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='python.exe' OR Name='pythonw.exe'"
        foreach ($radioHelper in $radioHelpers) {
            $radioCommand=$radioHelper.CommandLine
            $radioIsControlHelper=$radioHelper.Name -eq 'powershell.exe' -and
                ($radioCommand -like "* -File *$radioRoot\Start-RadioRelay.ps1*" -or $radioCommand -like "* -File *$radioRoot\Stop-RadioRelay.ps1*")
            $radioIsMonitor=$radioHelper.Name -like 'python*' -and $radioCommand -like "*$radioRoot\relay_status.py*" -and $radioCommand -like '*--output*relay-monitor.private.json*'
            if ($radioIsControlHelper -or $radioIsMonitor) {
                Stop-Process -Id $radioHelper.ProcessId -ErrorAction SilentlyContinue
                Wait-Process -Id $radioHelper.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
            }
        }
    }
    $script:radioResourcesClosed=$true
}
function Update-RadioMonitor {
    if ($script:radioMonitor -and $script:radioMonitor.HasExited) {
        $script:radioMonitor.Dispose();$script:radioMonitor=$null
        $script:radioNextMonitor=[DateTime]::UtcNow.AddSeconds(3)
        try {
            $radioSnapshot=Get-Content -LiteralPath "$radioState\relay-monitor.private.json" -Raw | ConvertFrom-Json
            if ($radioSnapshot.ok) {
                $serverPending.Text="$($radioSnapshot.server.pending_packets) 包  /  $($radioSnapshot.server.pending_observations) 条采样"
                $serverPending.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#185ABC')
                $radioKB=[Math]::Round($radioSnapshot.server.pending_bytes/1024,1)
                $radioArchive=if ($radioSnapshot.server.storage_mode -eq 'permanent') { "服务器存档：$($radioSnapshot.server.stored_packets) 包    " } else { '' }
                $serverDetail.Text="${radioArchive}待同步：$radioKB KB    $([DateTime]::Now.ToString('HH:mm:ss'))"
            } else {throw 'Server query failed.'}
        } catch {
            $serverPending.Text='未知（查询失败）'
            $serverPending.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#986800')
            $serverDetail.Text='正在重试；查询失败不会按 0 条处理。'
        }
    }
    if (-not $script:radioMonitor -and [DateTime]::UtcNow -ge $script:radioNextMonitor) {
        $script:radioMonitor=Start-Process -FilePath $radioPython -ArgumentList @('-X','utf8',(Quote-RadioPath "$radioRoot\relay_status.py"),'--output',(Quote-RadioPath "$radioState\relay-monitor.private.json")) -WindowStyle Hidden -RedirectStandardOutput "$radioState\monitor.log" -RedirectStandardError "$radioState\monitor-errors.log" -PassThru
    }
}
function Update-RadioControl {
    if ($script:radioOperation) {
        if (-not $script:radioOperation.HasExited) { return }
        $radioExit=$script:radioOperation.ExitCode
        $script:radioOperation.Dispose();$script:radioOperation=$null
        if ($radioExit -ne 0) {
            [System.Windows.Forms.MessageBox]::Show('操作未完成，请查看接收程序的控制日志。','飞机数据接收') | Out-Null
        }
    }
    $radioWorkers=@(Get-RadioManagedWorkers)
    $radioReceiver=@($radioWorkers | Where-Object Script -eq 'server.py').Count -gt 0
    $radioClient=@($radioWorkers | Where-Object Script -eq 'relay_client.py').Count -gt 0
    $script:radioIsRunning=$radioReceiver -and $radioClient
    $toggle.Enabled=$true
    if ($script:radioIsRunning) {
        $status.Text='● 接收服务已开启'
        $status.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#147D52')
        $toggle.Text='停止接收';$toggle.BackColor=[System.Drawing.ColorTranslator]::FromHtml('#C43D3D')
        try {
            $radioBackend=Get-Content -LiteralPath "$radioState\relay-client-status.private.json" -Raw | ConvertFrom-Json
            $radioClientPid=($radioWorkers | Where-Object Script -eq 'relay_client.py' | Select-Object -First 1).ProcessId
            if ($radioBackend.pid -eq $radioClientPid) {
                if ($radioBackend.phase -eq 'retrying') {
                    $status.Text='● 连接异常，等待重试'
                    $status.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#986800')
                } elseif ($radioBackend.server.pending_packets -eq 0) {
                    $status.Text="● 空队列确认中（$($radioBackend.empty_seconds)/10 秒）"
                }
            }
        } catch {}
    } else {
        $status.Text=if ($radioReceiver -or $radioClient) { '● 接收服务未完整启动' } elseif ((Get-RadioStopReason) -eq 'server_empty') { '● 已收完，自动停止' } else { '● 接收服务已停止' }
        $status.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#667085')
        $toggle.Text='启动接收';$toggle.BackColor=[System.Drawing.ColorTranslator]::FromHtml('#147D52')
    }
}
$toggle.Add_Click({
    $toggle.Enabled=$false
    $radioAction=if ($script:radioIsRunning) {'Stop-RadioRelay.ps1'} else {'Start-RadioRelay.ps1'}
    $status.Text=if ($script:radioIsRunning) {'正在停止接收…'} else {'正在启动接收…'}
    $radioArguments=@('-NoProfile','-ExecutionPolicy','Bypass','-File',(Quote-RadioPath "$radioRoot\$radioAction"))
    if ($radioAction -eq 'Start-RadioRelay.ps1') { $radioArguments+='-Manual' }
    try {
        $script:radioOperation=Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $radioArguments -WindowStyle Hidden -RedirectStandardOutput "$radioState\control-action.log" -RedirectStandardError "$radioState\control-action-errors.log" -PassThru
    } catch {
        $script:radioOperation=$null;$toggle.Enabled=$true
        [System.Windows.Forms.MessageBox]::Show('启动控制命令失败，请查看运行说明。','飞机数据接收') | Out-Null
    }
})
$timer=[System.Windows.Forms.Timer]::new();$timer.Interval=2000
$timer.Add_Tick({ Update-RadioMonitor; Update-RadioControl })
$form.Add_FormClosing({Close-RadioWindowResources})
if ($VerifyClose -or $VerifyCloseDuringStart) {
    $form.Opacity=0;$form.ShowInTaskbar=$false
    $form.Add_Shown({
        if ($VerifyCloseDuringStart) {$toggle.PerformClick()}
        $form.Close()
    })
}
try {
    Update-RadioControl
    Update-RadioMonitor
    if ($Preview) {
        $form.Opacity=0;$form.ShowInTaskbar=$false
        $form.Show();[System.Windows.Forms.Application]::DoEvents()
        $radioMonitorDeadline=[DateTime]::UtcNow.AddSeconds(15)
        while ($script:radioMonitor -and [DateTime]::UtcNow -lt $radioMonitorDeadline) {
            Start-Sleep -Milliseconds 100
            Update-RadioMonitor
            [System.Windows.Forms.Application]::DoEvents()
        }
        if ($VerifyControls) {
            foreach ($radioExpected in @($false,$true)) {
                $toggle.PerformClick()
                $radioDeadline=[DateTime]::UtcNow.AddSeconds(20)
                while ($script:radioOperation -and [DateTime]::UtcNow -lt $radioDeadline) {
                    [System.Windows.Forms.Application]::DoEvents()
                    Start-Sleep -Milliseconds 100
                    Update-RadioControl
                }
                if ($script:radioOperation -or $script:radioIsRunning -ne $radioExpected) {throw 'Desktop toggle verification failed.'}
                Write-Output "Desktop toggle verified: enabled=$radioExpected"
            }
        }
        $bitmap=[System.Drawing.Bitmap]::new($form.Width,$form.Height)
        $form.DrawToBitmap($bitmap,[System.Drawing.Rectangle]::new(0,0,$form.Width,$form.Height))
        $bitmap.Save("$radioState\control-preview.png",[System.Drawing.Imaging.ImageFormat]::Png)
        $bitmap.Dispose()
    } else {
        $timer.Start();$form.ShowDialog() | Out-Null
    }
} finally {
    Close-RadioWindowResources
    $timer.Stop();$timer.Dispose();$form.Dispose()
    if ($radioCreated) {$radioWindowLock.ReleaseMutex()}
    $radioWindowLock.Dispose()
}
