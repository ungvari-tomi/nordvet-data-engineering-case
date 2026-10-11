$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$envPath = Join-Path $repoRoot ".env"
$logDirectory = Join-Path $repoRoot "logs"

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logPath = Join-Path $logDirectory ("daily-extract-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$pipelineExitCode = 1

try {
    if (-not (Test-Path $pythonPath -PathType Leaf)) {
        throw "Python environment not found at '$pythonPath'."
    }
    if (-not (Test-Path $envPath -PathType Leaf)) {
        throw "Credentials file not found at '$envPath'."
    }

    "Started daily VetOne extract at $(Get-Date -Format o)" | Set-Content -Path $logPath
    Push-Location $repoRoot
    try {
        & $pythonPath -m src.pipeline *>> $logPath
        $pipelineExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    if ($pipelineExitCode -ne 0) {
        "Extract failed with exit code $pipelineExitCode at $(Get-Date -Format o)" |
            Add-Content -Path $logPath
        exit $pipelineExitCode
    }

    "Extract completed successfully at $(Get-Date -Format o)" | Add-Content -Path $logPath
    exit 0
}
catch {
    "Extract failed: $($_.Exception.Message)" | Add-Content -Path $logPath
    exit 1
}
