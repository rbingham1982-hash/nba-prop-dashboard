# The NBA + WNBA half of the daily parlay run, with the git sync around it.
#
# stats.nba.com does not answer cloud addresses, so these two leagues stay on this
# machine while .github/workflows/daily-board.yml runs MLB and NFL on GitHub. This side
# owns the NBA/WNBA shards, tip_log.json, nba_weekly_picks.json and the nba_data/ game-log
# snapshot, and commits nothing else; the cloud owns the MLB/NFL shards, game_log.json and weekly_picks.json.
#
# The sync is careful because this is also the working copy you edit in:
#   * it only syncs on main, so a run never commits to a feature branch;
#   * it never pushes commits of yours: if local main is ahead of origin, it skips;
#   * it skips if a cloud-owned log has local edits (the dashboard can log MLB parlays),
#     because rebasing over those would leave conflict markers in a 28 MB JSON file;
#   * it commits only its own files, whatever else is staged.
# A skipped sync is not lost work: the logs stay modified and the next run retries.
# Task Scheduler calls run_home_board.bat, which calls this.

Set-Location $PSScriptRoot
$log = Join-Path $PSScriptRoot "logs\home_board_sync.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Say($msg) {
    $line = "{0:yyyy-MM-dd HH:mm}  {1}" -f (Get-Date), $msg
    # Write-Host, not Write-Output: inside Test-CanSync, output joins the return value, and
    # ("sync skipped...", $false) is a non-empty array, which PowerShell treats as true.
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

$owned = @("parlay_log-NBA-*.json", "parlay_log-WNBA-*.json", "tip_log.json", "nba_weekly_picks.json", "form_log-NBA.json", "favorite_log-NBA.json", "favorite_log-WNBA.json", "threes_log-NBA.json")
$cloudOwned = @("parlay_log-MLB-*.json", "parlay_log-NFL-*.json", "game_log.json", "weekly_picks.json", "form_log-NFL.json", "favorite_log-NFL.json", "favorite_log-MLB.json")

function Test-CanSync {
    $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($branch -ne "main") { Say "sync skipped: on branch '$branch', not main"; return $false }
    if ((Test-Path .git\rebase-merge, .git\rebase-apply, .git\MERGE_HEAD) -contains $true) {
        Say "sync skipped: a rebase or merge is in progress"; return $false
    }
    git fetch -q origin main
    if ($LASTEXITCODE -ne 0) { Say "sync skipped: git fetch failed"; return $false }
    $ahead = [int](git rev-list --count origin/main..HEAD)
    if ($ahead -gt 0) { Say "sync skipped: main has $ahead unpushed commit(s) of yours"; return $false }
    $dirty = git status --porcelain -- $cloudOwned
    if ($dirty) { Say "sync skipped: cloud-owned logs have local edits: $($dirty -join '; ')"; return $false }
    return $true
}

$sync = Test-CanSync
if ($sync) {
    git pull -q --rebase --autostash origin main
    if ($LASTEXITCODE -ne 0) { Say "sync skipped: pull failed"; $sync = $false }
}

& venv\Scripts\python.exe daily_parlay_gen.py --sports nba,wnba --no-game-markets @args
$genExit = $LASTEXITCODE

# Save the league game logs into nba_data/ for the hosted dashboard, which stats.nba.com
# refuses (nba_snapshot.py). Only months that changed are rewritten.
& venv\Scripts\python.exe nba_snapshot.py
if ($LASTEXITCODE -ne 0) { Say "nba_snapshot export failed (exit $LASTEXITCODE)" }

if ($sync -and (Test-CanSync)) {
    $files = @($owned | ForEach-Object { Get-ChildItem -Name $_ -ErrorAction SilentlyContinue })
    $files += @(Get-ChildItem nba_data -Filter *.csv.gz -ErrorAction SilentlyContinue | ForEach-Object { "nba_data/$($_.Name)" })
    if ($files.Count -gt 0) {
        git add -- $files
        git diff --cached --quiet -- $files
        if ($LASTEXITCODE -ne 0) {
            git commit -q -m "chore: home board run $(Get-Date -Format 'yyyy-MM-dd HH:mm')" -- $files
            $pushed = $false
            foreach ($i in 1..3) {
                git pull -q --rebase --autostash origin main
                if ($LASTEXITCODE -eq 0) {
                    git push -q origin main
                    if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
                }
                Start-Sleep -Seconds (15 * $i)
            }
            if ($pushed) { Say "pushed: $($files -join ', ')" } else { Say "push failed after three tries" }
        } else {
            Say "nothing to commit"
        }
    }
}
exit $genExit
