#!/usr/bin/env bash
# Seed a fresh database with two small runs (instant mock latency) for the UI and API tests.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
mkdir -p "$here/.data"
rm -f "$here/.data/e2e.db"
export AGENTBENCH_DATABASE_URL="sqlite:///$here/.data/e2e.db"
export AGENTBENCH_MOCK_TIME_SCALE=0
cfg="$repo/configs/agentbench.yaml"
agentbench run -c "$cfg" --suite core --suite tool_use --suite isolation --limit 30 --label e2e-baseline >/dev/null
agentbench run -c "$cfg" --suite core --suite tool_use --suite isolation --limit 30 --seed 7 --label e2e-candidate >/dev/null
echo "seeded $AGENTBENCH_DATABASE_URL"
