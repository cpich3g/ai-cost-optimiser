<#
.SYNOPSIS
    Deploy the Azure Cost Optimiser Function App with safe durable instance purge.

.DESCRIPTION
    Before deploying new code, terminates all running/pending durable orchestration
    instances to prevent non-deterministic replay errors and phantom email loops.

.PARAMETER FunctionAppName
    Name of the Azure Function App (default: func-cost-optimiser-flex8029)

.PARAMETER ResourceGroup
    Resource group containing the Function App (default: rg-cost-optimiser)

.PARAMETER SkipPurge
    Skip the durable instance purge step

.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -SkipPurge
    .\deploy.ps1 -FunctionAppName my-func -ResourceGroup my-rg
#>

param(
    [string]$FunctionAppName = "func-cost-optimiser-flex8029",
    [string]$ResourceGroup = "rg-cost-optimiser",
    [switch]$SkipPurge
)

$ErrorActionPreference = "Stop"

Write-Host "`n=== Azure Cost Optimiser — Deploy ===" -ForegroundColor Cyan

# --- Step 1: Purge running durable instances ---
if (-not $SkipPurge) {
    Write-Host "`n[1/3] Purging running durable orchestration instances..." -ForegroundColor Yellow

    # Get the master key for the durabletask webhook
    $masterKey = az functionapp keys list `
        --name $FunctionAppName `
        --resource-group $ResourceGroup `
        --query "masterKey" -o tsv 2>$null

    if (-not $masterKey) {
        Write-Host "  WARNING: Could not retrieve master key. Skipping purge." -ForegroundColor Red
        Write-Host "  Ensure you are logged in: az login" -ForegroundColor Red
    }
    else {
        $baseUrl = "https://$FunctionAppName.azurewebsites.net/runtime/webhooks/durabletask"

        # Query for running and pending instances
        $statuses = @("Running", "Pending", "Suspended")
        $terminated = 0

        foreach ($status in $statuses) {
            $listUrl = "$baseUrl/instances?runtimeStatus=$status&code=$masterKey"
            try {
                $instances = Invoke-RestMethod -Uri $listUrl -Method GET -ErrorAction Stop
                foreach ($inst in $instances) {
                    $id = $inst.instanceId
                    $terminateUrl = "$baseUrl/instances/$id/terminate?reason=pre-deploy+purge&code=$masterKey"
                    try {
                        Invoke-WebRequest -Uri $terminateUrl -Method POST -ErrorAction Stop | Out-Null
                        Write-Host "  Terminated: $id ($status)" -ForegroundColor DarkYellow
                        $terminated++
                    }
                    catch {
                        # 410 Gone = already in terminal state, that's fine
                        if ($_.Exception.Response.StatusCode.value__ -eq 410) {
                            Write-Host "  Already terminal: $id" -ForegroundColor DarkGray
                        }
                        else {
                            Write-Host "  Failed to terminate $id : $($_.Exception.Message)" -ForegroundColor Red
                        }
                    }
                }
            }
            catch {
                Write-Host "  Could not list $status instances: $($_.Exception.Message)" -ForegroundColor Red
            }
        }

        if ($terminated -eq 0) {
            Write-Host "  No running instances found. Clean slate." -ForegroundColor Green
        }
        else {
            Write-Host "  Terminated $terminated instance(s)." -ForegroundColor Green
        }
    }
}
else {
    Write-Host "`n[1/3] Skipping purge (--SkipPurge)" -ForegroundColor DarkGray
}

# --- Step 2: Install dependencies ---
Write-Host "`n[2/3] Installing Python dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt --quiet 2>$null
Write-Host "  Dependencies up to date." -ForegroundColor Green

# --- Step 3: Deploy to Azure ---
Write-Host "`n[3/3] Publishing to Azure Functions..." -ForegroundColor Yellow
func azure functionapp publish $FunctionAppName --python

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n=== Deploy complete ===" -ForegroundColor Green
}
else {
    Write-Host "`n=== Deploy FAILED (exit code $LASTEXITCODE) ===" -ForegroundColor Red
    exit $LASTEXITCODE
}
