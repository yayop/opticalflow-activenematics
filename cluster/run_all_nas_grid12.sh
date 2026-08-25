#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/erosas/projects/opticalflow-activenematics}"
NAS_BASE='/volume1/homes/Edgardo Rosas/Bulk Active Nematics Videos'
cd "$PROJECT_DIR"

campaigns=(
  'bulk2|7200|1486|1064'
  '20241106_BULK001|2168|1486|1064'
  '20241108_BULK|2734|1486|1064'
  '20250227|1802|1486|1064'
  'BULK|3000|1486|1064'
)

for specification in "${campaigns[@]}"; do
  IFS='|' read -r name frames height width <<< "$specification"
  bash cluster/run_nas_grid12_campaign.sh \
    "$name" "$NAS_BASE/$name" "$frames" "$height" "$width"
done

echo 'All NAS grid-12 campaigns completed.'
