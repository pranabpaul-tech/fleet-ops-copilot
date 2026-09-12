# Fleet Ops Copilot

Real-time bus telemetry on Microsoft Fabric (Eventstream → Eventhouse), a
proactive Operations Agent, and a conversational Foundry agent in Teams — on
an F8 capacity behind Fabric Private Link, with the Foundry agent **VNet-injected**
into that same private network.

This is the implementation of the plan discussed in chat: Bicep for everything
that's ARM-native, Python for the Fabric workspace items and Foundry agent
setup that aren't. Nothing has been deployed yet — see the phases below.

## Layout

```
infra/           Bicep — 3 waves, see infra/README.md
src/fleetops/    Python — setup/, foundry/, actions/, validate/
artifacts/       KQL, Eventstream/Operations Agent definition templates
state.json       created at runtime — the seam between Bicep outputs and
                 Python-created resource IDs. Never commit real values from
                 a live deployment; .gitignore covers it.
```

## Before you start

1. `python -m venv .venv && .venv\Scripts\activate` (Windows) then
   `pip install -r requirements.txt`.
2. `copy .env.example .env` and fill in what you already know (the rest gets
   filled in as you go, via `state.json`).
3. Read `infra/README.md`'s prerequisites section — several things (tenant
   settings, resource provider registration) are portal-only and have to
   happen before Wave 1.

## Phases

| # | What | Where |
|---|---|---|
| 0 | Tenant prerequisites (Fabric admin portal, manual) | `infra/README.md` |
| 1 | Wave 1 Bicep: network, F8 capacity, tenant PL, Key Vault, monitoring, jumpbox, Foundry | `infra/main.bicep` + `deploy.ps1 -Wave 1` |
| 2 | Onto the jumpbox — everything from here runs inside the VNet | Bastion |
| 3 | Fabric workspace, Eventhouse, KQL schema, Eventstream | `src/fleetops/setup/01_workspace.py` → `04_eventstream.py` |
| 4 | Wave 2 Bicep (workspace private link) + network lockdown | `deploy.ps1 -Wave 2`, then `setup/05_network_policy.py --confirm` |
| 5 | Operations Agent (author in portal, capture, install Teams app) | `setup/06_ops_agent.py` |
| 6 | Foundry hosted agent + Toolbox + MCP tool — **validate this before continuing, see risk #1** | `mcp_tool.py`, `toolbox.py`, `deploy_hosted_agent.py` |
| 7 | Wave 3 Bicep (bot) + Teams publish | `deploy.ps1 -Wave 3`, then `foundry/publish_teams.py` |
| 8 | Validate | `python -m fleetops.validate.e2e_flow` |

Run every `setup/*.py` and `foundry/deploy_hosted_agent.py` / `06_ops_agent.py` from the
jumpbox, signed in as a real operator (`az login`) — not a service principal.
The Operations Agent inherits its creator's identity; `common/auth.py`'s
`assert_delegated_identity()` enforces this and fails fast otherwise.

## The one thing to validate first

`src/fleetops/foundry/mcp_tool.py`'s module docstring lays out risk #1 in
detail: whether Fabric's Eventhouse MCP endpoint is reachable, and
authenticable, from a VNet-injected Foundry agent talking to a
workspace-private-link-secured workspace. Nothing in Microsoft's docs
confirms this combination works. Exercise it in the Foundry playground
(phase 6) before building the rest of the demo on top of it. If it doesn't
work, the fallback is a custom tool that queries Kusto directly over the
private endpoint instead of going through MCP.

## Agent shape: MAF hosted agent, not a Prompt Agent

The Fleet Incident Agent is a **hosted agent** — real application code
(`foundry/hosted_agent/main.py`, using Microsoft Agent Framework's
`FoundryChatClient` + `FoundryToolbox`) packaged as a container and deployed
via `foundry/deploy_hosted_agent.py`, not a `PromptAgentDefinition` created
via `agents.create_version()`. Tools are wired through a Foundry **Toolbox**
(`foundry/toolbox.py`) wrapping the Fabric Eventhouse MCP connection, rather
than attaching MCP directly to the agent — the documented pattern for MAF
hosted agents specifically. Confirmed against a live private-network
reference deployment in this same subscription
([`pranabpaul-tech/foundry-iq-v2`](https://github.com/pranabpaul-tech/foundry-iq-v2)),
which also confirmed `wave3-bot.bicep` and `publish_teams.py` need no changes
for this — the Teams-publish flow is the same regardless of Prompt vs.
hosted agent.

Two things that reference caught that the generic docs got wrong or omitted:
- `project_connection_id` on an MCP tool must be the connection's **full ARM
  resource ID**, not its bare name — passing just the name is a silent bug.
- The connection's `audience` is `https://api.fabric.microsoft.com` (matching
  this endpoint's own host), not `https://analysis.windows.net/powerbi/api`
  from the generic MCP docs example (that was for a different Fabric path).

`mcp_tool.py` now creates the connection via a direct `az rest PUT`, not `azd
ai connection create` — sidesteps the Conditional-Access risk below entirely
for that one step.

## Second risk, found via a related community repo

[`anihitk07/foundry-hosted-agents-e2e-samples`](https://github.com/anihitk07/foundry-hosted-agents-e2e-samples)
validated hosted Foundry agents across the same network postures this project
uses, in the same class of environment (a Microsoft-internal "MCAPS" tenant —
we're in one too: `MngEnvMCAP072730`). Two of their findings apply directly:

- **Cosmos DB governance is a non-issue for us, not a blocker.** Their MCAPS
  tenant enforces an Azure Policy that silently reverts
  `publicNetworkAccess=Enabled` on Cosmos DB — this broke their *public*
  BYO-Cosmos posture. Our posture wants Cosmos private anyway (behind the
  private endpoint Wave 1 sets up), so this policy, if it also applies here,
  works with us rather than against us.
- **New risk: interactive sign-in from the jumpbox may be blocked.** They
  documented `azd`'s device-code sign-in getting rejected by Conditional
  Access ("your admin requires the device requesting access to be managed by
  Microsoft") in one MCAPS tenant, and separately that the beta
  `azure.ai.agents` extension has rejected a jumpbox VM's managed-identity
  token against a private Foundry project. Conditional Access policies
  requiring a managed device are typically enforced at the Entra token layer,
  not per-CLI, so this could affect `foundry/deploy_hosted_agent.py`'s `az login` /
  `DefaultAzureCredential` flow too, not just `azd`. If Phase 6 fails on sign-in
  rather than on the MCP call itself, this is the first thing to check —
  try an interactive Bastion RDP session with a real browser sign-in before
  concluding the MCP path (risk #1) is what's broken.

## Two things this repo deliberately doesn't do

- **No custom bot host.** `infra/wave3-bot.bicep` uses Foundry's native
  `SingleTenant` + agent-identity pattern, not a hand-rolled Container App
  running `botbuilder-*`. Foundry's publish flow reaches the same Teams
  surface with no container to run.
- **No Fabric Data Agent.** Kusto sources (which Eventhouse is) aren't
  supported for Fabric Data Agents under private link, so the conversational
  path goes through the remote Eventhouse MCP tool only.

## Capture workflows

Three files in `artifacts/` are placeholders with a `_placeholder: true`
marker, because their real shape isn't published anywhere — Fabric's
built-in "Buses" sample source's event schema, the Eventstream definition
JSON, and the `OperationsAgentV1` definition schema. Each has a matching
script with a `--capture` mode: author the thing once in the Fabric portal,
then pull its real definition down over the placeholder. See
`setup/04_eventstream.py` and `setup/06_ops_agent.py`.
