# Runs right after `azd provision` finishes Wave 1 (infra/main.bicep). Creates
# the Fabric workspace items that only exist as API calls, not ARM resources:
# workspace, Eventhouse, KQL schema, Eventstream. Everything after this point
# needs a step only a human can do (Fabric admin portal settings, authoring
# the Operations Agent) — see the README's "Manual steps" section for those.

$ErrorActionPreference = 'Stop'
$repoRoot = Resolve-Path "$PSScriptRoot/../.."

Push-Location $repoRoot
try {
    if (-not (Test-Path ".venv")) {
        Write-Host "Creating Python venv..." -ForegroundColor Cyan
        python -m venv .venv
    }
    & ".venv/Scripts/pip.exe" install -q -r requirements.txt

    $env:PYTHONPATH = "src"
    $env:FLEETOPS_FORCE_CLI_CREDENTIAL = "1"

    Write-Host "`n== Creating the Fabric workspace and assigning it to the capacity ==" -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" src/fleetops/setup/01_workspace.py

    Write-Host "`n== Creating the Eventhouse + KQL database ==" -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" src/fleetops/setup/02_eventhouse.py

    Write-Host "`n== Applying the BusTelemetry schema ==" -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" src/fleetops/setup/03_kql_schema.py

    Write-Host "`n== Creating the Eventstream (Buses sample source -> Eventhouse) ==" -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" src/fleetops/setup/04_eventstream.py --apply

    Write-Host "`nWave 1 + Fabric pipeline are live. See README.md 'Manual steps' for what's left:" -ForegroundColor Green
    Write-Host "  1. Enable two Fabric admin portal tenant settings, then run setup/05_network_policy.py --confirm" -ForegroundColor Yellow
    Write-Host "  2. Deploy infra/wave2-fabric-privatelink.bicep" -ForegroundColor Yellow
    Write-Host "  3. Run foundry/deploy_hosted_agent.py" -ForegroundColor Yellow
    Write-Host "  4. Deploy infra/wave3-bot.bicep, then run foundry/publish_teams.py" -ForegroundColor Yellow
    Write-Host "  5. Author the Operations Agent in the Fabric portal, then run setup/06_ops_agent.py --capture <id>" -ForegroundColor Yellow
}
finally {
    Pop-Location
}
