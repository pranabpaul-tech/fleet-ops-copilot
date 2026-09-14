#!/usr/bin/env bash
# Posix equivalent of preprovision.ps1 — see that file for what this does and why.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

prompt_with_default() {
  local message="$1" default="$2" value
  # read exits non-zero on EOF (no tty — CI, automation); with `set -e` that
  # would kill the whole script, so tolerate it and fall back to the default.
  read -r -p "$message [$default]: " value || true
  echo "${value:-$default}"
}

set_env_value() {
  local key="$1" value="$2"
  if [ -f .env ]; then
    grep -v "^$key=" .env > .env.tmp || true
    mv .env.tmp .env
  fi
  echo "$key=$value" >> .env
}

get_env_value() {
  local key="$1" default="$2"
  if [ -f .env ] && grep -q "^$key=" .env; then
    grep "^$key=" .env | cut -d= -f2-
  else
    echo "$default"
  fi
}

# azd env get-value prints its "key not found" error to STDOUT and, worse,
# still exits 0 — the only way to tell success from failure is the text
# itself. It can ALSO print an unrelated "Update available: ..." notice to
# stdout ahead of the real output. Confirmed live: both of these land in
# stdout, not stderr, so `2>/dev/null` alone doesn't help — strip known noise
# lines first, then treat what's left as the value (empty/ERROR = not found).
get_azd_value() {
  local key="$1" val
  val="$(azd env get-value "$key" 2>/dev/null | grep -v -E '^(Update available:|To update, run|ERROR:|Suggestion:|Run .azd env|$)')" || true
  echo "$val"
}

echo ""
echo "== Deployment names (press Enter to keep the default) =="

current_rg="$(get_azd_value AZURE_RESOURCE_GROUP_NAME)"
current_rg="${current_rg:-$(get_env_value FLEETOPS_RESOURCE_GROUP rg-fleet-ops-copilot)}"
rg="$(prompt_with_default "Resource group name" "$current_rg")"
azd env set AZURE_RESOURCE_GROUP_NAME "$rg" >/dev/null
set_env_value "FLEETOPS_RESOURCE_GROUP" "$rg"

current_foundry="$(get_azd_value FOUNDRY_AI_SERVICES_BASE_NAME)"
current_foundry="${current_foundry:-fleetopsai}"
foundry="$(prompt_with_default "Foundry account base name (a short suffix gets appended)" "$current_foundry")"
azd env set FOUNDRY_AI_SERVICES_BASE_NAME "$foundry" >/dev/null

current_fabric="$(get_azd_value FABRIC_CAPACITY_NAME)"
current_fabric="${current_fabric:-$(get_env_value FLEETOPS_FABRIC_CAPACITY_NAME fleetopsf8)}"
fabric="$(prompt_with_default "Fabric capacity name" "$current_fabric")"
azd env set FABRIC_CAPACITY_NAME "$fabric" >/dev/null
set_env_value "FLEETOPS_FABRIC_CAPACITY_NAME" "$fabric"

current_agent_name="$(get_env_value FLEETOPS_FOUNDRY_AGENT_NAME fleet-incident-agent)"
agent_name="$(prompt_with_default "Foundry hosted agent name (technical identifier, no spaces)" "$current_agent_name")"
set_env_value "FLEETOPS_FOUNDRY_AGENT_NAME" "$agent_name"

echo ""
echo "Using: resource group '$rg', Foundry base name '$foundry', Fabric capacity '$fabric', hosted agent '$agent_name'."
