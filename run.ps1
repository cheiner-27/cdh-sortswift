# Start cdh-sortswift.
# Production-ish mode: serves the built frontend from FastAPI at http://127.0.0.1:8000
# Use -Dev to also start the Vite dev server (hot reload) at http://localhost:5173
# Use -NoSync to skip pulling code updates from GitHub (e.g. when offline).
#
# The database stays on this machine; git no longer carries it. Committing it on
# every exit stored a full ~125MB copy per session in Git LFS and filled GitHub's
# storage quota. Instead backend/backup_db.py zips a copy into Google Drive on
# launch and on exit, while the app is stopped, so it is never copied mid-write.
param([switch]$Dev, [switch]$NoSync)

$root = $PSScriptRoot

# Paths whose arrival in a pull means the running app is stale. dist/ is
# gitignored (it's a build artifact), so a pull that changes frontend source
# leaves the previously-built bundle in place and you keep running the old UI
# until something forces a rebuild. That "something" is Update-Build.
$frontendPaths = @('frontend/src', 'frontend/public', 'frontend/index.html',
                   'frontend/package.json', 'frontend/vite.config.js')
$depsPath      = 'backend/uv.lock'

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

function Backup-Database {
    # backup_db.py prints its own result or warning; a failed backup never
    # stops the app from starting or exiting.
    Push-Location "$root\backend"
    try { uv run python backup_db.py }
    finally { Pop-Location }
}

function Sync-Down {
    # Pulls code only. Returns the list of repo paths the pull changed, so the
    # caller knows whether the frontend bundle or the Python deps need rebuilding.
    Write-Host "Pulling latest code from GitHub..." -ForegroundColor Cyan
    Push-Location $root
    try {
        $before = git rev-parse HEAD 2>$null
        git pull --no-edit
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: pull failed - you may be offline or have a conflict." -ForegroundColor Yellow
            Write-Host "         Launching on the code you already have." -ForegroundColor Yellow
            return @()
        }
        $after = git rev-parse HEAD 2>$null
        if ($before -eq $after) { return @() }
        # Say what arrived. The usual reason this matters is having pushed a
        # change from elsewhere and forgotten - seeing it land here is the
        # difference between "why is this the old screen" and "ah, right".
        $log = @(git log --oneline --no-merges "$before..$after" 2>$null)
        if ($log.Count -gt 0) {
            Write-Host "Pulled $($log.Count) update(s) from GitHub:" -ForegroundColor Green
            $log | Select-Object -First 8 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
            if ($log.Count -gt 8) { Write-Host "  ...and $($log.Count - 8) more" -ForegroundColor DarkGray }
        }
        return @(git diff --name-only $before $after 2>$null)
    }
    finally { Pop-Location }
}

function Update-Build {
    # Rebuild whatever the pull invalidated. Skipped in -Dev: Vite is watching
    # the source itself, so a production build would just be wasted time.
    param([string[]]$Changed)
    if (-not $Changed -or $Changed.Count -eq 0) { return }

    if ($Changed -contains $depsPath) {
        Write-Host "Python dependencies changed - installing..." -ForegroundColor Yellow
        Push-Location "$root\backend"
        try {
            uv sync -q
            if ($LASTEXITCODE -ne 0) {
                Write-Host "WARNING: uv sync failed - the app may not start." -ForegroundColor Red
            }
        }
        finally { Pop-Location }
    }
    if ($Dev) { return }

    $touched = $false
    foreach ($path in $Changed) {
        foreach ($watched in $frontendPaths) {
            if ($path -like "$watched*") { $touched = $true }
        }
    }
    if (-not $touched) { return }
    Write-Host "Frontend changed on GitHub - rebuilding..." -ForegroundColor Yellow
    Push-Location "$root\frontend"
    try {
        if ($Changed -contains 'frontend/package.json') { npm install }
        npm run build
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: build failed - serving the previous bundle." -ForegroundColor Red
        }
    }
    finally { Pop-Location }
}

$doSync = (-not $NoSync) -and (Test-SyncReady)
if ($NoSync)               { Write-Host "Code update skipped (-NoSync)." -ForegroundColor DarkGray }
elseif (-not $doSync)      { Write-Host "Code update skipped (no git 'origin' remote here)." -ForegroundColor DarkGray }

if ($doSync) {
    $changed = Sync-Down
    Update-Build -Changed $changed
}

# Catches a session whose exit backup never ran (window closed with the X).
Backup-Database

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
    uv run python -m uvicorn app.main:app --port 8000
}
finally {
    # Runs when you stop the app (Ctrl+C). If this is ever skipped (e.g. you close
    # the window with the X), the next launch's backup catches up automatically.
    Backup-Database
}
