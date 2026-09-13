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
or a manual step that only exists after the previous one runs.

**Where to run these from:** every command below is a plain script under
`src/fleetops/`, run with `python <path>` **from the repo root** of your
clone (each script adds its own `src/` to `sys.path`, so no install step or
`PYTHONPATH` is needed) — e.g. on Windows:

```powershell
.venv\Scripts\python.exe src\fleetops\setup\05_network_policy.py --confirm
```

or on macOS/Linux:

```bash
.venv/bin/python src/fleetops/setup/05_network_policy.py --confirm
```

Some of these are plain Azure/Fabric **control-plane** REST calls, signed in
as yourself — they don't need to run from inside the VNet, so your own
machine (wherever you ran `azd provision` from) is fine (marked **local**
below). The rest either test the workspace's *private* endpoint from inside
the network, or call the Foundry account's own data-plane API — and the
Foundry account has public access disabled from the start (it's
VNet-injected), so those genuinely fail with `403 Public access is disabled`
from anywhere but inside the VNet (marked **jumpbox** below). Run those from
the jumpbox — copy the repo onto it once (`curl`+`tar`, since `git clone`
doesn't reliably complete over `az container exec` — see the ACI jumpbox
notes further down), then:

```bash
az container exec --resource-group <rg> --name ci-fleetops-jump \
  --container-name jumpbox --exec-command "python3 /path/to/script.py"
```

| # | Step | Where | Script (path relative to repo root) |
|---|---|---|---|
| 1 | Flip two Fabric admin portal tenant settings (see **Manual steps**) | — | — |
| 2 | Deploy the workspace-level Fabric private link | local | `./infra/deploy.ps1 -Wave 2` |
| 3a | Verify its DNS resolves privately | jumpbox | `src/fleetops/validate/network_check.py` |
| 3b | Lock the workspace down to private-only access | local | `src/fleetops/setup/05_network_policy.py --confirm` |
| 4 | Deploy the Foundry hosted agent (grants it Kusto access automatically) | jumpbox | `src/fleetops/foundry/deploy_hosted_agent.py` |
| 5 | Deploy the Bot Service | local | `./infra/deploy.ps1 -Wave 3` |
| 6 | Publish the agent to Microsoft Teams | jumpbox | `src/fleetops/foundry/publish_teams.py` |
| 7 | Author the Operations Agent (see **Manual steps**) | local | `src/fleetops/setup/06_ops_agent.py --capture <id>` |
| 8 | Validate everything end to end | jumpbox | `src/fleetops/validate/e2e_flow.py` |

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
- **Authoring the Operations Agent** — its schema isn't fully documented, so
  this repo captures a real definition from the portal rather than guessing
  one:
  1. In the [Fabric portal](https://app.fabric.microsoft.com), open your
     workspace (`fleet-ops-copilot` by default) and create a new
     **Operations Agent** item. Point its data source at the Eventhouse's
     KQL database (the one created in step 2 of provisioning).
  2. Its item ID is in the browser's URL bar once you have it open —
     something like
     `.../operationsagents/46a2c551-1f9c-4a2d-9620-27c6a3a0524e`; the GUID
     at the end is `<id>`.
  3. From the repo root, on your own machine (this is a plain Fabric REST
     call, not a VNet one):
     ```powershell
     .venv\Scripts\python.exe src\fleetops\setup\06_ops_agent.py --capture <id>
     ```
     This writes the real definition into
     `artifacts/ops-agent/OperationsAgentV1.json`.
  4. To push edited instructions from that file back up to the same agent
     later, run `src/fleetops/setup/06_ops_agent.py --update <id>` the same
     way (`--apply` only ever *creates* a brand-new agent — it's a no-op once
     one is already recorded in `state.json`).
  5. Back in the portal, click **Generate Playbook** on the agent, then
     **Start** it — both are portal-only actions with no API equivalent.
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
