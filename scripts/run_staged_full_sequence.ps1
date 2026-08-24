[CmdletBinding()]
param(
    [string]$SourceDir = '\\ACTNEM\homes\Edgardo Rosas\Bulk Active Nematics Videos\Bulk_1_12_11\ImageSequence',
    [string]$FrameTemplate = 'Frame_{0:D4}.tif',
    [string]$PythonFramePattern = 'Frame_{index:04d}.tif',
    [string]$RunName = 'raw_full_dense_f16',
    [int]$ExpectedFrameCount = 7200,
    [int]$FrameHeight = 1578,
    [int]$FrameWidth = 1120,
    [double]$MaxStageGiB = 5.0,
    [ValidateSet('float16', 'float32')]
    [string]$FlowDtype = 'float16',
    [string]$SshTarget = 'swift',
    [string]$RemoteProject = '/home/erosas/projects/opticalflow-activenematics',
    [switch]$PlanOnly,
    [switch]$KeepRemoteArtifacts,
    [switch]$RetryFailedJobs
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$stagingRoot = Join-Path $repoRoot ".staging\$RunName"
$destinationRoot = Join-Path $repoRoot "results\Bulk_1_12_11\$RunName"
$remoteInputRoot = "$RemoteProject/data/staged_full_sequences/$RunName"
$remoteOutputRoot = "$RemoteProject/results/staged_full_sequences/$RunName"

function Assert-LocalChildPath {
    param([string]$Candidate, [string]$AllowedRoot)
    $candidateFull = [IO.Path]::GetFullPath($Candidate)
    $rootFull = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\', '/')
    $prefix = $rootFull + [IO.Path]::DirectorySeparatorChar
    if (-not $candidateFull.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing operation outside $rootFull`: $candidateFull"
    }
    return $candidateFull
}

function Remove-LocalBatchDirectory {
    param([string]$Path, [string]$AllowedRoot)
    if (Test-Path -LiteralPath $Path) {
        $checked = Assert-LocalChildPath -Candidate $Path -AllowedRoot $AllowedRoot
        Remove-Item -LiteralPath $checked -Recurse -Force
    }
}

function Remove-LocalStateFile {
    param([string]$Path, [string]$AllowedRoot)
    if (Test-Path -LiteralPath $Path) {
        $checked = Assert-LocalChildPath -Candidate $Path -AllowedRoot $AllowedRoot
        Remove-Item -LiteralPath $checked -Force
    }
}

function Assert-RemoteChildPath {
    param([string]$Candidate, [string]$AllowedRoot)
    $prefix = $AllowedRoot.TrimEnd('/') + '/'
    if (-not $Candidate.StartsWith($prefix, [StringComparison]::Ordinal)) {
        throw "Refusing remote operation outside $AllowedRoot`: $Candidate"
    }
    if ($Candidate -notmatch '^[A-Za-z0-9_./-]+$') {
        throw "Remote path contains unsupported characters: $Candidate"
    }
}

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE"
    }
}

function Invoke-SshText {
    param([string]$Command)
    $output = & ssh $SshTarget $Command
    if ($LASTEXITCODE -ne 0) {
        throw "ssh failed with exit code $LASTEXITCODE"
    }
    return ($output -join "`n").Trim()
}

function Get-SlurmJobState {
    param([string]$JobId)
    $state = Invoke-SshText "sacct -j '$JobId' --format=State -n -X | head -n 1 | xargs"
    if (-not $state) {
        return ''
    }
    return (($state -split '\s+')[0].TrimEnd('+'))
}

function Test-DownloadedBatch {
    param(
        [string]$Path,
        [int]$PairStart,
        [int]$PairEnd
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return $false
    }
    & python (Join-Path $PSScriptRoot 'verify_sequence_batch.py') `
        $Path `
        --pair-start $PairStart `
        --pair-end $PairEnd `
        --dtype $FlowDtype `
        --height $FrameHeight `
        --width $FrameWidth
    return $LASTEXITCODE -eq 0
}

if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
    throw "Source directory does not exist: $SourceDir"
}
if ($MaxStageGiB -le 0) {
    throw 'MaxStageGiB must be positive.'
}

$sourceFiles = @(Get-ChildItem -LiteralPath $SourceDir -File -Filter '*.tif' | Sort-Object Name)
if ($sourceFiles.Count -ne $ExpectedFrameCount) {
    throw "Expected $ExpectedFrameCount TIFF files, found $($sourceFiles.Count)."
}
for ($index = 1; $index -le $sourceFiles.Count; $index++) {
    $expectedName = $FrameTemplate -f $index
    if ($sourceFiles[$index - 1].Name -cne $expectedName) {
        throw "Unexpected frame at index $index`: $($sourceFiles[$index - 1].Name), expected $expectedName"
    }
}

$maxStageBytes = [int64]($MaxStageGiB * 1GB)
$batches = [Collections.Generic.List[object]]::new()
$frameStart = 1
while ($frameStart -lt $sourceFiles.Count) {
    $frameEnd = $frameStart
    $stageBytes = [int64]$sourceFiles[$frameStart - 1].Length
    while ($frameEnd -lt $sourceFiles.Count) {
        $nextBytes = [int64]$sourceFiles[$frameEnd].Length
        if ($stageBytes + $nextBytes -gt $maxStageBytes) {
            break
        }
        $stageBytes += $nextBytes
        $frameEnd++
    }
    if ($frameEnd -le $frameStart) {
        throw "Stage limit cannot hold two frames at frame $frameStart."
    }
    $pairStart = $frameStart
    $pairEnd = $frameEnd - 1
    $batchName = 'batch_{0:D4}_{1:D4}' -f $pairStart, $pairEnd
    $batches.Add([pscustomobject]@{
        Name = $batchName
        FrameStart = $frameStart
        FrameEnd = $frameEnd
        PairStart = $pairStart
        PairEnd = $pairEnd
        StageBytes = $stageBytes
    })
    $frameStart = $frameEnd
}

$estimatedMiBPerPair = if ($FlowDtype -eq 'float16') { 6.2 } else { 13.0 }
$totalPairCount = $sourceFiles.Count - 1
$verifiedPairs = 0
foreach ($batch in $batches) {
    $reportPath = Join-Path (Join-Path $destinationRoot $batch.Name) 'verification.json'
    if (Test-Path -LiteralPath $reportPath) {
        try {
            $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
            if ($report.verified -and $report.dtype -eq $FlowDtype -and
                $report.pair_start -eq $batch.PairStart -and
                $report.pair_end -eq $batch.PairEnd) {
                $verifiedPairs += 1 + $batch.PairEnd - $batch.PairStart
            }
        } catch {
            Write-Warning "Ignoring unreadable verification report: $reportPath"
        }
    }
}
$remainingPairs = $totalPairCount - $verifiedPairs
$estimatedOutputBytes = [int64]($remainingPairs * $estimatedMiBPerPair * 1MB)
$drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($destinationRoot))
$requiredFreeBytes = $estimatedOutputBytes + $maxStageBytes + 5GB
if ($drive.AvailableFreeSpace -lt $requiredFreeBytes) {
    throw ('Insufficient local space. Estimated requirement {0:N1} GiB; available {1:N1} GiB.' -f `
        ($requiredFreeBytes / 1GB), ($drive.AvailableFreeSpace / 1GB))
}

$batches | Select-Object Name, FrameStart, FrameEnd, PairStart, PairEnd, `
    @{Name='StageGiB';Expression={[math]::Round($_.StageBytes / 1GB, 3)}} | Format-Table -AutoSize
Write-Host ('Estimated remaining fields: {0:N1} GiB ({1}); verified pairs: {2}' -f `
    ($estimatedOutputBytes / 1GB), $FlowDtype, $verifiedPairs)
if ($PlanOnly) {
    Write-Host 'Plan-only mode: no files transferred and no jobs submitted.'
    exit 0
}

New-Item -ItemType Directory -Force -Path $stagingRoot, $destinationRoot | Out-Null
Invoke-SshText "mkdir -p '$remoteInputRoot' '$remoteOutputRoot' '$RemoteProject/logs'" | Out-Null

foreach ($batch in $batches) {
    $localFinal = Join-Path $destinationRoot $batch.Name
    $localPartial = Join-Path $destinationRoot ('.partial_' + $batch.Name)
    $localStage = Join-Path $stagingRoot $batch.Name
    $jobRecordPath = Join-Path $stagingRoot ($batch.Name + '.job.json')
    $remoteBatchInput = "$remoteInputRoot/$($batch.Name)"
    $remoteBatchOutput = "$remoteOutputRoot/$($batch.Name)"
    Assert-RemoteChildPath $remoteBatchInput $remoteInputRoot
    Assert-RemoteChildPath $remoteBatchOutput $remoteOutputRoot

    if (Test-DownloadedBatch $localFinal $batch.PairStart $batch.PairEnd) {
        Write-Host "Verified batch already present; skipping: $($batch.Name)"
        if (-not $KeepRemoteArtifacts) {
            Invoke-SshText "rm -rf -- '$remoteBatchInput' '$remoteBatchOutput'" | Out-Null
        }
        Remove-LocalBatchDirectory $localStage $stagingRoot
        Remove-LocalStateFile $jobRecordPath $stagingRoot
        continue
    }

    Remove-LocalBatchDirectory $localPartial $destinationRoot
    $jobId = $null
    if (Test-Path -LiteralPath $jobRecordPath) {
        $jobRecord = Get-Content -LiteralPath $jobRecordPath -Raw | ConvertFrom-Json
        if ($jobRecord.batch -ne $batch.Name) {
            throw "Job record does not match batch: $jobRecordPath"
        }
        $jobId = [string]$jobRecord.job_id
        $recordedState = Get-SlurmJobState $jobId
        if ($recordedState -notin @('PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'COMPLETED', '')) {
            if (-not $RetryFailedJobs) {
                throw "Recorded job $jobId ended in state $recordedState. Remote artifacts were retained. Use -RetryFailedJobs after diagnosis."
            }
            Write-Warning "Retrying failed job $jobId for $($batch.Name); retained batch artifacts will be replaced."
            Remove-LocalStateFile $jobRecordPath $stagingRoot
            $jobId = $null
        }
        if ($jobId) {
            Write-Host "Resuming recorded Slurm job $jobId for $($batch.Name): $recordedState"
        }
    }
    if (-not $jobId) {
        Remove-LocalBatchDirectory $localStage $stagingRoot
        New-Item -ItemType Directory -Path $localStage | Out-Null
        Write-Host "Staging $($batch.Name) locally..."
        for ($index = $batch.FrameStart; $index -le $batch.FrameEnd; $index++) {
            Copy-Item -LiteralPath $sourceFiles[$index - 1].FullName -Destination $localStage
        }

        Invoke-SshText "rm -rf -- '$remoteBatchInput' '$remoteBatchOutput'; mkdir -p '$remoteInputRoot' '$remoteOutputRoot'" | Out-Null
        Write-Host "Uploading $($batch.Name)..."
        Invoke-Checked scp @('-r', $localStage, "${SshTarget}:$remoteInputRoot/")

        $export = 'ALL,INPUT_DIR={0},OUTPUT_DIR={1},PAIR_START={2},PAIR_END={3},FRAME_PATTERN={4},FLOW_DTYPE={5},OVERLAY_EVERY=0,GRID_STEP=24' -f `
            $remoteBatchInput, $remoteBatchOutput, $batch.PairStart, $batch.PairEnd, `
            $PythonFramePattern, $FlowDtype
        $jobName = 'of_{0:D4}_{1:D4}' -f $batch.PairStart, $batch.PairEnd
        $jobId = Invoke-SshText "cd '$RemoteProject' && sbatch --parsable --job-name='$jobName' --export='$export' cluster/run_sequence_batch.slurm"
        if ($jobId -notmatch '^\d+$') {
            throw "Unexpected sbatch response: $jobId"
        }
        [ordered]@{
            batch = $batch.Name
            job_id = $jobId
            remote_input = $remoteBatchInput
            remote_output = $remoteBatchOutput
        } | ConvertTo-Json | Set-Content -LiteralPath $jobRecordPath -Encoding utf8
        Write-Host "Submitted Slurm job $jobId for $($batch.Name)."
    }

    do {
        Start-Sleep -Seconds 20
        $state = Get-SlurmJobState $jobId
        Write-Host "Job $jobId`: $state"
    } while ($state -in @('PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', ''))
    if ($state -ne 'COMPLETED') {
        throw "Job $jobId ended in state $state. Remote inputs and outputs were retained."
    }

    Write-Host "Downloading fields for $($batch.Name)..."
    Invoke-Checked scp @('-r', "${SshTarget}:$remoteBatchOutput", $localPartial)
    if (-not (Test-DownloadedBatch $localPartial $batch.PairStart $batch.PairEnd)) {
        throw "Downloaded verification failed for $($batch.Name). Remote artifacts were retained."
    }
    if (Test-Path -LiteralPath $localFinal) {
        throw "Final batch path exists but is invalid: $localFinal"
    }
    $checkedPartial = Assert-LocalChildPath $localPartial $destinationRoot
    Move-Item -LiteralPath $checkedPartial -Destination $localFinal

    if (-not $KeepRemoteArtifacts) {
        Invoke-SshText "rm -rf -- '$remoteBatchInput' '$remoteBatchOutput'" | Out-Null
    }
    Remove-LocalBatchDirectory $localStage $stagingRoot
    Remove-LocalStateFile $jobRecordPath $stagingRoot
    Write-Host "Completed and cleaned $($batch.Name)."
}

$allRows = foreach ($batch in $batches) {
    Import-Csv -LiteralPath (Join-Path (Join-Path $destinationRoot $batch.Name) 'summary.csv')
}
$allRows | Sort-Object pair | Export-Csv -LiteralPath (Join-Path $destinationRoot 'summary.csv') -NoTypeInformation
$verificationReports = foreach ($batch in $batches) {
    Get-Content -LiteralPath (Join-Path (Join-Path $destinationRoot $batch.Name) 'verification.json') -Raw | ConvertFrom-Json
}
$manifest = [ordered]@{
    run_name = $RunName
    source_dir = $SourceDir
    frame_count = $sourceFiles.Count
    pair_count = $totalPairCount
    max_stage_gib = $MaxStageGiB
    flow_dtype = $FlowDtype
    overlays_generated = $false
    batches = $verificationReports
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $destinationRoot 'manifest.json') -Encoding utf8
Write-Host "Full staged run verified: $destinationRoot"
