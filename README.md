# Fleet Ops Copilot

Real-time bus fleet telemetry, ingested and analyzed on Microsoft Fabric, watched
proactively by a Fabric Operations Agent, and explorable conversationally through
a Foundry-hosted agent published to Microsoft Teams — all on a Fabric F8 capacity
with the conversational agent VNet-injected and the Fabric workspace reachable
over a private link.

## What it does

- **Ingests** live bus telemetry from Fabric's built-in "Buses" sample source
  through an Eventstream into a Fabric Eventhouse (KQL database), flattening
  raw events into a typed `BusTelemetry` table.
- **Monitors** that table continuously with a Fabric **Operations Agent**,
  which detects delay incidents, dwell/holding incidents, and vehicles that
  stop reporting, and sends an alert for each one.
- **Answers questions** through a **Foundry-hosted conversational agent**,
  published to Microsoft Teams, that queries `BusTelemetry` directly via its
  own custom Kusto tool to ground its answers in live data.

## Architecture

```mermaid
flowchart LR
    subgraph Fabric["Microsoft Fabric — F8 capacity"]
        direction TB
        Buses(["Buses sample source"]) --> ES["Eventstream"]
        ES --> Raw[("BusTelemetryRaw")]
        Raw -- "update policy" --> Flat[("BusTelemetry")]
        Flat --> OpsAgent["Operations Agent"]
    end

    subgraph VNet["Azure VNet"]
        direction TB
        subgraph AgentSubnet["snet-agent"]
            HostedAgent["Foundry hosted agent<br/>(custom Kusto tool)"]
        end
        subgraph ContainerSubnet["snet-container"]
            Jumpbox["Jumpbox (Container Instance)"]
        end
        subgraph PeSubnet["snet-pe"]
            PE1["Fabric workspace<br/>private endpoint"]
            PE2["Key Vault<br/>private endpoint"]
        end
    end

    HostedAgent -- "queries live data" --> Flat
    OpsAgent -- "alert" --> Recipient(["Teams / email recipient"])
    HostedAgent --> Bot["Bot Service"]
    Bot -- "publish" --> Teams(["Microsoft Teams"])
    Jumpbox -. "management &amp; validation" .-> Fabric
```

- **Microsoft Fabric** — an F8 capacity hosts the workspace, Eventhouse, and
  Operations Agent. The workspace is reachable over a private link
  (`infra/wave2-fabric-privatelink.bicep`).
- **Azure VNet** — three subnets: `snet-agent` (the Foundry account is
  VNet-injected here), `snet-pe` (private endpoints for the Fabric workspace
  and Key Vault), and `snet-container` (a jumpbox reachable via
  `az container exec` — no RDP/Bastion needed).
- **Foundry hosted agent** — real application code (Microsoft Agent
  Framework), not a declarative prompt agent. Its one tool queries the
  Eventhouse directly using its own granted identity.
- **Bot Service + Teams** — the hosted agent is published to Teams through a
  Bot Service registration.

## Deploy

### Prerequisites

- [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) (`azd`) and the Azure CLI (`az`), both signed in
  (`azd auth login`, `az login`) as a real user — not a service principal.
  Several steps (creating the Operations Agent, the network lockdown) inherit
  their creator's identity and require a delegated human sign-in.
- Python 3.11+.
- An existing Azure AI Search service, Storage account, and Cosmos DB account
  — Foundry's standard agent setup requires all three. If you don't have
  them, `infra/wave0-byo-resources.bicep` creates minimal ones:

  ```bash
  az deployment group create --resource-group <rg> --template-file infra/wave0-byo-resources.bicep
  ```

- A region that isn't `eastus` (the Operations Agent isn't available there)
  and that supports Fabric capacities and Foundry VNet injection.

### 1. Configure

```bash
git clone <this-repo>
cd fleet-ops-copilot
cp .env.example .env   # fill in values
```

Fill in `infra/main.bicepparam`: your Fabric admin UPN(s), your own operator
object ID (`az ad signed-in-user show --query id -o tsv`), and the resource
IDs of the AI Search / Storage / Cosmos DB accounts from the prerequisites
step.

### 2. Provision the core infrastructure

```bash
azd provision
```

This deploys the VNet, F8 Fabric capacity, Key Vault, monitoring, the
jumpbox, and the VNet-injected Foundry account/project — then a
`postprovision` hook automatically creates the Fabric workspace, Eventhouse,
KQL schema, and Eventstream, so telemetry starts flowing right after
`azd provision` finishes.

### 3. Finish the remaining stages

The rest can't be folded into one atomic deployment — each stage needs an ID
or a manual step that only exists after the previous one runs. Run these in
order (from the jumpbox: `az container exec --resource-group <rg> --name
ci-fleetops-jump --container-name jumpbox --exec-command "..."`, or from any
machine signed in with `az login`, for anything that only needs a delegated
Azure/Fabric REST call rather than direct network access):

| # | Step | Command |
|---|---|---|
| 1 | Flip two Fabric admin portal tenant settings (see **Manual steps**) | — |
| 2 | Lock down the workspace to private access | `python -m fleetops.setup.05_network_policy --confirm` |
| 3 | Deploy the workspace-level Fabric private link | `./infra/deploy.ps1 -Wave 2` |
| 4 | Deploy the Foundry hosted agent (grants it Kusto access automatically) | `python -m fleetops.foundry.deploy_hosted_agent` |
| 5 | Deploy the Bot Service | `./infra/deploy.ps1 -Wave 3` |
| 6 | Publish the agent to Microsoft Teams | `python -m fleetops.foundry.publish_teams` |
| 7 | Author the Operations Agent (see **Manual steps**) | `python -m fleetops.setup.06_ops_agent --capture <id>` |
| 8 | Validate everything end to end | `python -m fleetops.validate.e2e_flow` |

## Manual steps required

These can't be scripted — they need a human in a portal, or a real
interactive sign-in:

- **Fabric admin portal tenant settings**: enable **Azure Private Link** and
  **Configure workspace-level inbound network rules**, and whatever
  Copilot / Azure OpenAI tenant settings the Operations Agent needs.
- **Resource provider registration**: `Microsoft.Fabric`,
  `Microsoft.BotService`, `Microsoft.App`, `Microsoft.CognitiveServices`,
  `Microsoft.Search`, `Microsoft.DocumentDB`, `Microsoft.Storage`,
  `Microsoft.KeyVault`. Re-register `Microsoft.Fabric` again the first time
  you use workspace-level private link — it has its own registration flag.
- **Authoring the Operations Agent**: create it once in the Fabric portal
  (point it at the Eventhouse's KQL database), then run
  `setup/06_ops_agent.py --capture <id>` to pull its real definition into
  this repo — its schema isn't fully documented, so this repo captures it
  from a live one rather than guessing. You can then push updated
  instructions to it via `setup/06_ops_agent.py --apply`, but the compiled
  **playbook** (from clicking **Generate Playbook**) and actually **starting**
  the agent are portal-only actions with no API equivalent.
- **Delegated sign-in**: every setup/validation script must run under a real
  operator's own `az login` session, not a service principal — the
  Operations Agent inherits its creator's identity, and the workspace
  network lockdown is sensitive enough that it should always have a human's
  fingerprints on it.

## Repo layout

```
infra/           Bicep — 3 waves (see infra/README.md), plus the ACI jumpbox
                 and Foundry account/project
infra/hooks/     azd postprovision hooks — run the Fabric pipeline setup
                 scripts automatically after `azd provision`
src/fleetops/    Python — setup/ (Fabric items), foundry/ (hosted agent +
                 Teams publish), actions/ (approval-gated operations),
                 validate/ (smoke tests + full e2e check)
artifacts/       KQL schema, Eventstream definition, Operations Agent
                 instructions
state.json       created at runtime — the seam between Bicep outputs and
                 Python-created resource IDs. Never commit real values;
                 .gitignore covers it.
```
