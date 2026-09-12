# Fleet Ops Copilot — infra

Three Bicep waves, because two of them need IDs that don't exist until a
Python step or a human portal step has run. See the repo root README for the
full phase-by-phase walkthrough; this file is the infra-specific reference.

## Wave 1 — `main.bicep`

Network (VNet + 4 subnets), F8 Fabric capacity, tenant-level Fabric private
link, Key Vault, Log Analytics + App Insights, jumpbox + Bastion, and the
VNet-injected Foundry account/project (vendored from
`modules/foundry-vendored/`, the real Microsoft sample at
`foundry-samples/infrastructure/infrastructure-setup-bicep/15-private-network-standard-agent-setup`
— don't hand-edit anything under `foundry-vendored/`, see its own
`VENDORED_FROM.md`).

**Before deploying:** fill in `main.bicepparam` (Fabric admin UPNs, your
operator object ID, jumpbox credentials, and the resource IDs of an existing
AI Search / Storage / Cosmos DB account — the Foundry standard agent setup
refuses to build without all three).

```powershell
./deploy.ps1 -Wave 1
```

Despite the vendored sample's own README instructing you to run
`createCapHost.sh` afterward, on a live deploy (2026-09-11) both the account-
and project-level capability hosts came back `provisioningState: Succeeded`
automatically — `addProjectCapabilityHost` isn't actually gated behind
`createAccountCapabilityHost` the way the param name implies. Confirm with:

```bash
az rest --method get --url "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>/capabilityHosts?api-version=2025-04-01-preview"
```

Only run `createCapHost.sh` if that comes back empty.

## Wave 2 — `wave2-fabric-privatelink.bicep`

Needs `workspaceId` from `state.json` (`setup/01_workspace.py` writes it).
Run `setup/01_workspace.py` through `04_eventstream.py` first, **then**:

```powershell
./deploy.ps1 -Wave 2
```

Verify before locking anything down — `python -m fleetops.validate.network_check`
from the jumpbox should show the workspace FQDN resolving to a private IP. A
fresh capacity can take up to 24 hours to appear in the private DNS zone; a
failure here in the first day usually means "wait," not "broken."

## Wave 3 — `wave3-bot.bicep`

Needs `msaAppId` and `activityEndpoint` from `state.json['foundry_agent']`
(`foundry/deploy_hosted_agent.py` writes them, after `mcp_tool.py --apply` and
`toolbox.py`). Run that, exercise the agent in the Foundry playground, **then**:

```powershell
./deploy.ps1 -Wave 3
```

Then `python -m fleetops.foundry.publish_teams` to actually publish it —
Bicep only creates the Bot Service resource; the PATCH and
`microsoft365/publish` REST calls it depends on live in that script.

## Prerequisites Bicep can't do for you (Fabric admin portal, manual)

- Register resource providers: `Microsoft.Fabric`, `Microsoft.BotService`,
  `Microsoft.App`, `Microsoft.CognitiveServices`, `Microsoft.Search`,
  `Microsoft.DocumentDB`, `Microsoft.Storage`, `Microsoft.KeyVault`.
  Re-register `Microsoft.Fabric` again the first time you use
  *workspace*-level private link — it has its own registration flag.
- Fabric admin portal → tenant settings: enable **Azure Private Link**
  (~15 min to propagate) and **Configure workspace-level inbound network
  rules**, and whatever Copilot / Azure OpenAI tenant settings the
  Operations Agent needs.
- Pick a region that isn't `eastus` — the Operations Agent isn't available
  there — and that's co-regional with your Foundry model.

## What's genuinely uncertain here

- **Workspace-level Fabric private link + Foundry's MCP tool.** Unvalidated
  — see `foundry/mcp_tool.py`'s module docstring. This is the reason
  `validate/e2e_flow.py` treats the MCP turn check as a warning, not a hard
  failure.
- **Operations Agent's `OperationsAgentV1` definition schema.** Thinly
  documented — `setup/06_ops_agent.py` uses the author-in-portal,
  capture-the-definition workflow rather than guessing the shape.
- **The built-in "Buses" sample source's actual event schema.** Same
  capture workflow, see `setup/04_eventstream.py` and
  `artifacts/kql/02_update_policy.kql`.
