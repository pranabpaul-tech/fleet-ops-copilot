#!/usr/bin/env bash
# Posix equivalent of postprovision.ps1 — see that file for what this does and why.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

if [ ! -d .venv ]; then
  echo "Creating Python venv..."
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

export PYTHONPATH=src
export FLEETOPS_FORCE_CLI_CREDENTIAL=1

echo "== Recording Wave 1's Bicep outputs into state.json =="
.venv/bin/python scripts/capture_wave1_state.py

echo "== Creating the Fabric workspace and assigning it to the capacity =="
.venv/bin/python src/fleetops/setup/01_workspace.py

echo "== Creating the Eventhouse + KQL database =="
.venv/bin/python src/fleetops/setup/02_eventhouse.py

echo "== Applying the BusTelemetry schema =="
.venv/bin/python src/fleetops/setup/03_kql_schema.py

echo "== Creating the Eventstream (Buses sample source -> Eventhouse) =="
.venv/bin/python src/fleetops/setup/04_eventstream.py --apply

cat <<'EOF'

Wave 1 + Fabric pipeline are live. See README.md "Manual steps" for what's left:
  1. Enable two Fabric admin portal tenant settings, then run setup/05_network_policy.py --confirm
  2. Deploy infra/wave2-fabric-privatelink.bicep
  3. Run foundry/deploy_hosted_agent.py
  4. Deploy infra/wave3-bot.bicep, then run foundry/publish_teams.py
  5. Author the Operations Agent in the Fabric portal, then run setup/06_ops_agent.py --capture <id>
EOF
