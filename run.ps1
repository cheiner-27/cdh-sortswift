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

# Paths whose arrival in a pull means the running app is stale. dist/ is
# gitignored (it's a build artifact), so a pull that changes frontend source
# leaves the previously-built bundle in place and you keep running the old UI
# until something forces a rebuild. That "something" is Update-Build.
$frontendPaths = @('frontend/src', 'frontend/public', 'frontend/index.html',
                   'frontend/package.json', 'frontend/vite.config.js')
$depsPath      = 'backend/requirements.txt'

function Get-Upstream {
    # The tracked remote branch (usually origin/main), or $null if none.
    $u = git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return $u
}

function Test-MidMerge {
    git rev-parse -q --verify MERGE_HEAD *> $null
    return ($LASTEXITCODE -eq 0)
}

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
        # Never commit while a merge is unresolved. `git add <db>` would mark the
        # conflicted database as resolved and the commit below would finish the
        # merge, silently declaring this machine's copy the winner and burying
        # the other machine's session inside a "Data sync" commit.
        if (Test-MidMerge) {
            Write-Host "STOPPED: an unresolved merge is in progress - not committing." -ForegroundColor Red
            Write-Host "         Your data is safe in $dbRel and in the backup beside it." -ForegroundColor Yellow
            Write-Host "         Resolve it before the next sync (see Sync-Down's message)." -ForegroundColor Yellow
            return
        }
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
    # Returns the list of repo paths the pull changed, so the caller knows
    # whether the frontend bundle or the Python deps need rebuilding.
    Write-Host "Syncing latest from GitHub..." -ForegroundColor Cyan
    Push-Location $root
    try {
        if (Test-MidMerge) {
            Write-Host "STOPPED: last sync left an unresolved merge." -ForegroundColor Red
            Write-Host "         Fix it once with either of these, then relaunch:" -ForegroundColor Yellow
            Write-Host "           git checkout --ours   $dbRel   # keep THIS machine's data" -ForegroundColor Yellow
            Write-Host "           git checkout --theirs $dbRel   # keep the OTHER machine's data" -ForegroundColor Yellow
            Write-Host "         then: git add $dbRel; git commit" -ForegroundColor Yellow
            return @()
        }
        # First push up anything a previous session left behind (e.g. after a crash
        # where the exit sync didn't run). In normal one-machine-at-a-time use this
        # is a no-op.
        Sync-Up -Quiet

        $before = git rev-parse HEAD 2>$null
        git fetch 2>$null
        $upstream = Get-Upstream
        if ($upstream) {
            # A SQLite file has no usable merge: .gitattributes marks it merge=lfs,
            # and no such driver is installed, so if both machines committed data
            # since they last agreed, the pull leaves a conflicted 108MB binary
            # that git cannot resolve either way. Detect that BEFORE pulling, while
            # the working tree is still clean and the local DB is still intact.
            $base = git merge-base HEAD $upstream 2>$null
            if ($base) {
                $ours   = git diff --name-only $base HEAD      -- $dbRel 2>$null
                $theirs = git diff --name-only $base $upstream -- $dbRel 2>$null
                if ($ours -and $theirs) {
                    $backup = "$root\backend\data\sortswift.conflict-$(Get-Date -Format 'yyyyMMdd-HHmmss').db"
                    Copy-Item "$root\$dbRel" $backup -ErrorAction SilentlyContinue
                    Write-Host "STOPPED: both machines changed the database since they last agreed." -ForegroundColor Red
                    Write-Host "         A SQLite file cannot be merged - one side has to win, and" -ForegroundColor Yellow
                    Write-Host "         that is your call, not the script's. Nothing was pulled." -ForegroundColor Yellow
                    Write-Host "         This machine's copy is backed up to:" -ForegroundColor Yellow
                    Write-Host "           $backup" -ForegroundColor Yellow
                    Write-Host "         Launching on local data; sync is off for this session." -ForegroundColor Yellow
                    $script:syncBlocked = $true
                    return @()
                }
            }
        }

        git pull --no-edit
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: pull failed - you may be offline or have a conflict." -ForegroundColor Yellow
            Write-Host "         Launching anyway; your data is still saved locally." -ForegroundColor Yellow
            return @()
        }
        $after = git rev-parse HEAD 2>$null
        if ($before -eq $after) { return @() }
        # Say what arrived. The usual reason this matters is having pushed a
        # change from the other machine and forgotten - seeing it land here is
        # the difference between "why is this the old screen" and "ah, right".
        $log = @(git log --oneline --no-merges "$before..$after" 2>$null |
                 Where-Object { $_ -notmatch 'Data sync ' })
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
        python -m pip install -q -r "$root\$depsPath"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: pip install failed - the app may not start." -ForegroundColor Red
        }
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

$script:syncBlocked = $false
$doSync = (-not $NoSync) -and (Test-SyncReady)
if ($NoSync)               { Write-Host "Data sync skipped (-NoSync)." -ForegroundColor DarkGray }
elseif (-not $doSync)      { Write-Host "Data sync skipped (no git 'origin' remote here)." -ForegroundColor DarkGray }

if ($doSync) {
    $changed = Sync-Down
    Update-Build -Changed $changed
    # A blocked sync means we never pulled, so pushing on exit would publish a
    # database built on a stale base. Run read-only for this session instead.
    if ($script:syncBlocked) { $doSync = $false }
}

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
