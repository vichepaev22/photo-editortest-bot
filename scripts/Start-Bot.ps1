param([ValidateSet('Demo', 'OpenAI')][string]$Mode = 'Demo')
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
$TaskPython = Join-Path $TaskRoot '.venv\Scripts\python.exe'
Push-Location -LiteralPath $TaskRoot
try {
    $Current = (& $TaskPython -m image_studio status) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read local bot status.' }
    if ($Current.running) {
        Write-Output "Bot already running: @$($Current.bot_username), mode=$($Current.mode). Stop it before changing mode."
        exit 0
    }
    $TaskRuntime = Join-Path $TaskRoot 'data\runtime'
    New-Item -ItemType Directory -Path $TaskRuntime -Force | Out-Null
    $SavedEnvironment = @{}
    $TaskValues = @{
        IMAGE_PROVIDER = $(if ($Mode -eq 'Demo') { 'mock' } else { 'openai' })
        DATA_DIR = $(if ($Mode -eq 'Demo') { 'data/demo' } else { 'data/live-pilot' })
        ENABLE_DEMO_CREDITS = $(if ($Mode -eq 'Demo') { 'true' } else { 'false' })
        BILLING_ENABLED = 'false'
        PYTHONUTF8 = '1'
    }
    if ($Mode -eq 'Demo') { $TaskValues['ENABLE_TRIAL_ACCESS'] = 'false' }
    try {
        foreach ($Name in $TaskValues.Keys) {
            $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
            [Environment]::SetEnvironmentVariable($Name, $TaskValues[$Name], 'Process')
        }
        $TaskProcess = Start-Process -FilePath $TaskPython -ArgumentList @('-m', 'image_studio', 'run') `
            -WorkingDirectory $TaskRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $TaskRuntime 'stdout.log') `
            -RedirectStandardError (Join-Path $TaskRuntime 'stderr.log')
    }
    finally {
        foreach ($Name in $SavedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name], 'Process')
        }
    }
    $Deadline = [DateTime]::UtcNow.AddSeconds(35)
    do {
        Start-Sleep -Milliseconds 500
        $Current = (& $TaskPython -m image_studio status) | ConvertFrom-Json
        if ($Current.running -and $Current.state -eq 'ready') {
            Write-Output "Bot ready: https://t.me/$($Current.bot_username), mode=$($Current.mode), pid=$($Current.pid)."
            exit 0
        }
        $TaskProcess.Refresh()
        if ($TaskProcess.HasExited) { throw 'Bot exited during startup. See data/runtime/stderr.log.' }
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw 'Startup is still pending. Check scripts/Status-Bot.ps1 and data/runtime/stderr.log.'
}
finally { Pop-Location }
