# Fleet Ops Copilot

Real-time bus telemetry on Microsoft Fabric (Eventstream → Eventhouse), a
proactive Operations Agent, and a conversational Foundry agent in Teams — on
an F8 capacity behind Fabric Private Link, with the Foundry agent **VNet-injected**
into that same private network.

This is the implementation of the plan discussed in chat: Bicep for everything
that's ARM-native, Python for the Fabric workspace items and Foundry agent
setup that aren't. Phases 0–6 are live and deployed (see the phases below) —
telemetry is flowing end to end and the hosted agent answers real queries
against it; phases 7–8 (Teams publish, final validation) are next.

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
| 6 | Foundry hosted agent + custom Kusto function tool (risk #1 resolved — see below) | `deploy_hosted_agent.py` (`mcp_tool.py`/`toolbox.py` are historical — MCP path doesn't work here) |
| 7 | Wave 3 Bicep (bot) + Teams publish | `deploy.ps1 -Wave 3`, then `foundry/publish_teams.py` |
| 8 | Validate | `python -m fleetops.validate.e2e_flow` |

Run every `setup/*.py` and `foundry/deploy_hosted_agent.py` / `06_ops_agent.py` from the
jumpbox, signed in as a real operator (`az login`) — not a service principal.
The Operations Agent inherits its creator's identity; `common/auth.py`'s
`assert_delegated_identity()` enforces this and fails fast otherwise.

## Risk #1 — resolved, and the answer is instructive

The original open question: is Fabric's Eventhouse MCP endpoint reachable,
and authenticable, from a VNet-injected Foundry agent talking to a
workspace-private-link-secured workspace? **Confirmed live: no, not via
MCP/Toolbox.** The chain of findings, in order:

1. `project_connection_id` on an MCP tool must be the connection's **full ARM
   resource ID**, not its bare name — passing just the name is a silent bug,
   not a validation error.
2. The connection's `audience` is `https://api.fabric.microsoft.com` (matching
   this endpoint's own host), not `https://analysis.windows.net/powerbi/api`
   from the generic MCP docs example (that was for a different Fabric path).
3. `UserEntraToken` auth fails outside a real Teams/interactive session with
   `"User identity authentication ... requires a delegated Microsoft Entra
   user context"` — switched to `AgenticIdentityToken` (the agent's own
   identity; granted it `Viewer` on the Fabric workspace to match).
4. Past all of that, the call still fails — with `"Name or service not
   known (api.fabric.microsoft.com:443)"`. **DNS resolution itself fails**
   from within Foundry's own MCP-calling infrastructure. A network-isolated
   Foundry account apparently can't reach *any* public-internet MCP endpoint
   this way, only ones exposed through an actual private endpoint — matching
   what the docs hinted ("private MCP requires a dedicated MCP subnet for a
   *self-hosted* server") but had never stated for a third party's public
   endpoint.

**The fix, live and verified**: skip MCP/Toolbox entirely. `foundry/hosted_agent/main.py`
has a plain `@tool`-decorated function (`query_bus_telemetry`) that calls
`azure-kusto-data` directly, in-process, using the agent's own identity. This
works because the agent's *own container code* has normal outbound
networking (it's how it reaches the model service at all) — the failure was
specific to Foundry's separate, more restricted MCP-proxy component, not the
agent's own network path. Verified end to end: the agent generated its own
KQL, queried live Eventhouse data, and gave a correct grounded answer citing
real telemetry.

`mcp_tool.py` and `toolbox.py` are kept as-is for the diagnostic trail (their
module docstrings tell this whole story) but are no longer part of the
working path — `deploy_hosted_agent.py` no longer depends on either.

## Agent shape: MAF hosted agent, not a Prompt Agent

The Fleet Incident Agent is a **hosted agent** — real application code
(`foundry/hosted_agent/main.py`, using Microsoft Agent Framework's
`FoundryChatClient` + a custom function tool) packaged as a container and
deployed via `foundry/deploy_hosted_agent.py`, not a `PromptAgentDefinition`
created via `agents.create_version()`. Confirmed against a live
private-network reference deployment in this same subscription
([`pranabpaul-tech/foundry-iq-v2`](https://github.com/pranabpaul-tech/foundry-iq-v2)),
which also confirmed `wave3-bot.bicep` and `publish_teams.py` need no changes
for this — the Teams-publish flow is the same regardless of Prompt vs.
hosted agent.

## ACI jumpbox — `az container exec`, no RDP needed

`infra/modules/aci-jumpbox.bicep` adds a Container Instance in a new
`snet-container` subnet (delegated to `Microsoft.ContainerInstance/containerGroups`),
reachable via `az container exec` — the Azure control plane, not a network
path, so it works the same whether the caller is a human or an agent.
Mirrors `pranabpaul-tech/foundry-iq-v2`'s own jumpbox pattern. Its
system-assigned identity needs **Foundry Project Manager** on the Foundry
account (confirmed live: `Cognitive Services Contributor` does *not* cover
agent/toolbox writes — that role grants `Microsoft.CognitiveServices/*` as a
**dataAction**, which does). Bootstrap notes: `az container exec` has no
shell behind it (arguments are split on whitespace, no quoting respected,
5000-char command limit) — `git clone` doesn't reliably complete over it, so
pull the repo via `curl`+`tar` from a public GitHub archive URL instead, and
use `scripts/write_b64_file.py` (two plain argv tokens, no whitespace) to
write file content that would otherwise need shell redirection.

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
