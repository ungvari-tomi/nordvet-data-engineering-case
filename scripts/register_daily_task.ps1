[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$At = "07:00",
    [string]$TaskName = "Nordvet Daily VetOne Extract"
)

$scheduleTime = [TimeSpan]::Zero
if (-not [TimeSpan]::TryParse($At, [ref]$scheduleTime)) {
    throw "Invalid time '$At'. Use 24-hour HH:mm format, for example 07:00."
}
if ($scheduleTime -lt [TimeSpan]::Zero -or $scheduleTime -ge [TimeSpan]::FromDays(1)) {
    throw "Invalid time '$At'. Use 24-hour HH:mm format, for example 07:00."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$envPath = Join-Path $repoRoot ".env"
$runnerPath = Join-Path $PSScriptRoot "run_daily_extract.ps1"

if (-not (Test-Path $pythonPath -PathType Leaf)) {
    throw "Python environment not found at '$pythonPath'. Create .venv and install requirements first."
}
if (-not (Test-Path $envPath -PathType Leaf)) {
    throw "Credentials file not found at '$envPath'. Create it from .env.example first."
}

$actionArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $runnerPath
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $actionArguments `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At ([datetime]::Today.Add($scheduleTime))
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$description = "Fetch yesterday's VetOne appointments into the local Nordvet SQLite database."

if ($PSCmdlet.ShouldProcess($TaskName, "Register daily run at $At")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $description `
        -Force | Out-Null
    Write-Output "Registered '$TaskName' to run daily at $At for the current user."
}
