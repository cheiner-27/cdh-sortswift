# Start cdh-sortswift.
# Production-ish mode: serves the built frontend from FastAPI at http://127.0.0.1:8000
# Use -Dev to also start the Vite dev server (hot reload) at http://localhost:5173
# Use -NoSync to skip the automatic GitHub data sync (e.g. when offline).
#
# Data sync: on launch this pulls the latest sortswift.db (and code) from GitHub,
# and on exit it commits + pushes the database back. Sync only happens while the
# app is closed, so the SQLite file is never copied mid-write. You never type git.
param([switch]$Dev, [switch]$NoSync)

$root  = $PSScriptRoot
$dbRel = 'backend/data/sortswift.db'   # relative to $root, forward slashes for git

function Test-SyncReady {
    # True only if $root is a git repo that has an 'origin' remote.
    Push-Location $root
    try {
        git rev-parse --is-inside-work-tree *> $null
        if ($LASTEXITCODE -ne 0) { return $false }
        $remotes = git remote 2>$null
        return ($remotes -contains 'origin')
    }
    finally { Pop-Location }
}

function Sync-Up {
    # Commit + push the database if it changed. Safe to call anytime.
    param([switch]$Quiet)
    Push-Location $root
    try {
        git add -- $dbRel 2>$null
        git diff --cached --quiet -- $dbRel
        if ($LASTEXITCODE -ne 0) {
            $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
            git commit -q -m "Data sync $stamp on $env:COMPUTERNAME"
            Write-Host "Pushing data to GitHub..." -ForegroundColor Cyan
            git push
            if ($LASTEXITCODE -ne 0) {
                Write-Host "WARNING: push failed - data is committed locally and will retry next launch." -ForegroundColor Yellow
            }
        }
        elseif (-not $Quiet) {
            Write-Host "No data changes to sync." -ForegroundColor DarkGray
        }
    }
    finally { Pop-Location }
}

function Sync-Down {
    Write-Host "Syncing latest from GitHub..." -ForegroundColor Cyan
    Push-Location $root
    try {
        # First push up anything a previous session left behind (e.g. after a crash
        # where the exit sync didn't run). In normal one-machine-at-a-time use this
        # is a no-op.
        Sync-Up -Quiet
        git pull --no-edit
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: pull failed - you may be offline or have a conflict." -ForegroundColor Yellow
            Write-Host "         Launching anyway; your data is still saved locally." -ForegroundColor Yellow
        }
    }
    finally { Pop-Location }
}

$doSync = (-not $NoSync) -and (Test-SyncReady)
if ($NoSync)               { Write-Host "Data sync skipped (-NoSync)." -ForegroundColor DarkGray }
elseif (-not $doSync)      { Write-Host "Data sync skipped (no git 'origin' remote here)." -ForegroundColor DarkGray }

if ($doSync) { Sync-Down }

if ($Dev) {
    Start-Process powershell -ArgumentList '-NoExit', '-Command',
        "cd '$root\frontend'; npm run dev"
}
elseif (-not (Test-Path "$root\frontend\dist")) {
    Write-Host "No frontend build found - building once..." -ForegroundColor Yellow
    Push-Location "$root\frontend"
    npm run build
    Pop-Location
}

Write-Host "Backend + UI: http://127.0.0.1:8000" -ForegroundColor Green
Set-Location "$root\backend"
try {
    python -m uvicorn app.main:app --port 8000
}
finally {
    # Runs when you stop the app (Ctrl+C). If this is ever skipped (e.g. you close
    # the window with the X), the next launch's Sync-Down catches up automatically.
    if ($doSync) { Sync-Up }
}
