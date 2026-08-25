[CmdletBinding()]
param(
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$denseSource = Join-Path $repoRoot 'results\Bulk_1_12_11\raw_full_dense_f16'
$bulk1NasResult = '\\ACTNEM\homes\Edgardo Rosas\Bulk Active Nematics Videos\Bulk_1_12_11\OpticalFlow_RAFT_grid12'

Write-Host 'Bulk_1_12_11: derive grid 12 from the existing dense fields (no GPU).'
Write-Host "  $denseSource -> $bulk1NasResult"
Write-Host 'Five remaining datasets: run directly NAS <-> swift with verified rsync.'

if ($PlanOnly) {
    Write-Host 'Plan-only mode: no files written and no jobs submitted.'
    exit 0
}

& python (Join-Path $PSScriptRoot 'derive_grid_sequence.py') `
    $denseSource $bulk1NasResult --grid-step 12 --dtype float16
if ($LASTEXITCODE -ne 0) {
    throw 'Bulk_1_12_11 dense-to-grid12 derivation failed.'
}

& ssh swift "cd /home/erosas/projects/opticalflow-activenematics && nohup bash cluster/run_all_nas_grid12.sh > logs/all_nas_grid12.out 2> logs/all_nas_grid12.err < /dev/null & echo `$!"
if ($LASTEXITCODE -ne 0) {
    throw 'Could not start the NAS campaign controller on swift.'
}
Write-Host 'NAS campaign controller started on swift.'
