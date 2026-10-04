param([switch]$BuildOnly, [string]$StackRoot = 'C:\Bambuddy')

$ErrorActionPreference = 'Stop'
$stack = $StackRoot
$fork = Join-Path $stack 'Bambuddy_OrcaAPI'
$api = Join-Path $stack 'OrcaSlicer_API'
$compose = Join-Path $stack 'compose.yaml'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$image = "bambuddy-orcaapi:$stamp"
$backup = Join-Path $stack "Backups\fork-deployment-$stamp"
$node = Join-Path $api 'runtime\node.exe'
if (-not (Test-Path -LiteralPath $node)) { throw 'The sidecar runtime is missing.' }

function Invoke-Docker {
    & docker.exe @args
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE)." }
}
function Wait-Healthy([string]$Url, [int]$Seconds = 120) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "Health check failed: $Url"
}

if (-not (Test-Path (Join-Path $fork 'backend\app\services\desktop_profile_sync.py'))) {
    throw 'The fork does not contain the desktop sync feature.'
}
Write-Output 'Building the fork. The running Bambuddy stays available during the build.'
Invoke-Docker build -t $image $fork
Push-Location $api
try {
    & $node node_modules/typescript/bin/tsc
    if ($LASTEXITCODE -ne 0) { throw 'Sidecar TypeScript build failed.' }
    & $node node_modules/tsc-esm-fix/target/esm/cli.mjs dist
    if ($LASTEXITCODE -ne 0) { throw 'Sidecar module build failed.' }
} finally { Pop-Location }
if ($BuildOnly) { Write-Output "Built $image. Deployment skipped."; return }

New-Item -ItemType Directory -Path $backup -Force | Out-Null
Copy-Item -LiteralPath $compose -Destination (Join-Path $backup 'compose.yaml')
Copy-Item -LiteralPath (Join-Path $api '.env') -Destination (Join-Path $backup 'sidecar.env')
$syncEnv = Join-Path $stack 'desktop-sync.env'
$hadSyncEnv = Test-Path -LiteralPath $syncEnv
if ($hadSyncEnv) { Copy-Item -LiteralPath $syncEnv -Destination (Join-Path $backup 'desktop-sync.env') }
$container = (& docker compose -f $compose ps -q bambuddy).Trim()
if ($LASTEXITCODE -ne 0 -or -not $container) { throw 'Start the existing Bambuddy before deploying so its image can be retained for rollback.' }
$previousImage = (& docker inspect --format '{{.Image}}' $container).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not identify the previous image.' }
$rollbackImage = "bambuddy-rollback:$stamp"
Invoke-Docker image tag $previousImage $rollbackImage
@{ previous_image = $previousImage; rollback_image = $rollbackImage; new_image = $image } | ConvertTo-Json | Set-Content (Join-Path $backup 'images.json')

$stopped = $false
$dataBackedUp = $false
try {
    Write-Output "Backing up Bambuddy to $backup"
    Invoke-Docker compose -f $compose stop bambuddy
    $stopped = $true
    $source = [IO.Path]::GetFullPath((Join-Path $stack 'data\bambuddy'))
    $target = [IO.Path]::GetFullPath((Join-Path $backup 'bambuddy-data'))
    $allowed = [IO.Path]::GetFullPath($backup).TrimEnd('\') + '\'
    if (-not $target.StartsWith($allowed, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid backup destination.' }
    & robocopy $source $target /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -gt 7) { throw 'Bambuddy data backup failed.' }
    $dataBackedUp = $true

    $tokenLine = @(Get-Content (Join-Path $api '.env') | Where-Object { $_ -match '^DESKTOP_PROFILE_SYNC_TOKEN=' })
    if ($tokenLine.Count) { $token = $tokenLine[0].Substring('DESKTOP_PROFILE_SYNC_TOKEN='.Length) }
    else {
        $bytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $token = [Convert]::ToBase64String($bytes)
    }
    "DESKTOP_PROFILE_SYNC_TOKEN=$token" | Set-Content $syncEnv -Encoding ascii
    $envLines = @(Get-Content (Join-Path $api '.env') | Where-Object { $_ -notmatch '^DESKTOP_(PROFILE_SYNC_TOKEN|PROFILES_PATH)=' })
    $envLines += 'DESKTOP_PROFILES_PATH=C:/Users/3leme/AppData/Roaming/OrcaSlicer/user/5e5d6945-094f-439c-bad1-a5399dcb4267'
    $envLines += "DESKTOP_PROFILE_SYNC_TOKEN=$token"
    $envLines | Set-Content (Join-Path $api '.env') -Encoding utf8
    $configuration = Get-Content $compose -Raw
    $configuration = ([regex]'(?m)^(    image: ).*$').Replace($configuration, ('$1' + $image), 1)
    if ($configuration -notmatch 'desktop-sync\.env') {
        $configuration = $configuration.Replace('  bambuddy:', "  bambuddy:`r`n    env_file:`r`n      - ./desktop-sync.env")
    }
    $configuration | Set-Content $compose -Encoding utf8
    Invoke-Docker compose -f $compose config --quiet
    Stop-ScheduledTask -TaskName 'Bambuddy Orca API'
    & (Join-Path $api 'scripts\stop-windows.ps1')
    Start-ScheduledTask -TaskName 'Bambuddy Orca API'
    Wait-Healthy 'http://127.0.0.1:3003/health'
    Invoke-Docker compose -f $compose up -d --no-deps --pull never bambuddy
    Wait-Healthy 'http://127.0.0.1:8000/health'
    Wait-Healthy 'http://127.0.0.1:8000/api/v1/local-presets/desktop-sync/status'
    Invoke-Docker image tag $image bambuddy-orcaapi:local
    Write-Output "Deployment healthy. Backup: $backup"
    Write-Output 'Open Profiles > Local Profiles > Sync desktop Orca profiles.'
} catch {
    $failure = $_
    Copy-Item -LiteralPath (Join-Path $backup 'compose.yaml') -Destination $compose -Force
    # Pin rollback to the image actually used before deployment, rather than a mutable remote tag.
    $rollback = Get-Content $compose -Raw
    $rollback = ([regex]'(?m)^(    image: ).*$').Replace($rollback, ('$1' + $rollbackImage), 1)
    $rollback | Set-Content $compose -Encoding utf8
    Copy-Item -LiteralPath (Join-Path $backup 'sidecar.env') -Destination (Join-Path $api '.env') -Force
    if ($hadSyncEnv) { Copy-Item -LiteralPath (Join-Path $backup 'desktop-sync.env') -Destination $syncEnv -Force }
    if ($stopped) {
        if ($dataBackedUp) {
            Invoke-Docker compose -f $compose stop bambuddy
            $liveData = [IO.Path]::GetFullPath((Join-Path $stack 'data\bambuddy'))
            $dataRoot = [IO.Path]::GetFullPath((Join-Path $stack 'data')).TrimEnd('\') + '\'
            $failedData = [IO.Path]::GetFullPath((Join-Path $backup 'failed-deployment-data'))
            $backupRoot = [IO.Path]::GetFullPath($backup).TrimEnd('\') + '\'
            if (-not $liveData.StartsWith($dataRoot, [StringComparison]::OrdinalIgnoreCase) -or
                -not $failedData.StartsWith($backupRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid rollback paths.' }
            Move-Item -LiteralPath $liveData -Destination $failedData
            Copy-Item -LiteralPath (Join-Path $backup 'bambuddy-data') -Destination $liveData -Recurse
        }
        Invoke-Docker compose -f $compose up -d --no-deps --pull never bambuddy
        Stop-ScheduledTask -TaskName 'Bambuddy Orca API'
        & (Join-Path $api 'scripts\stop-windows.ps1')
        Start-ScheduledTask -TaskName 'Bambuddy Orca API'
    }
    throw "Deployment failed and the previous image/configuration was restored. Data backup: $backup. Reason: $failure"
}
