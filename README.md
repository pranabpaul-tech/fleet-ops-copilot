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

Curious *why* any of this is built the way it is — timing-sensitive steps,
platform quirks, non-obvious error messages? See **[How it works](#how-it-works)**
at the bottom. Everything below this point is just what to do.

## Deploy

### Prerequisites

- [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) (`azd`) and the Azure CLI (`az`), both signed in
  (`azd auth login`, `az login`) as a real user — not a service principal.
- Python 3.11+.
- An existing Azure AI Search service, Storage account, and Cosmos DB account.
  If you don't have them:
  ```bash
  az deployment group create --resource-group <rg> --template-file infra/wave0-byo-resources.bicep
  ```
- A region that isn't `eastus`, that supports Fabric capacities and Foundry
  VNet injection.
- Two Fabric admin portal tenant settings enabled, before you start: **Azure
  Private Link** and **Configure workspace-level inbound network rules**
  (plus whatever Copilot / Azure OpenAI tenant settings your tenant needs for
  the Operations Agent).
- The following resource providers registered in your subscription:
  `Microsoft.Fabric`, `Microsoft.BotService`, `Microsoft.App`,
  `Microsoft.CognitiveServices`, `Microsoft.Search`, `Microsoft.DocumentDB`,
  `Microsoft.Storage`, `Microsoft.KeyVault`.

### Step 1 — Configure

```bash
git clone <this-repo>
cd fleet-ops-copilot
cp .env.example .env   # fill in values
```

Fill in `infra/main.bicepparam`: your Fabric admin UPN(s), your own operator
object ID (`az ad signed-in-user show --query id -o tsv`), and the resource
IDs of the AI Search / Storage / Cosmos DB accounts from the prerequisites
step.

### Step 2 — Provision everything Fabric-side

```bash
azd provision
```

**Result:** the VNet, F8 Fabric capacity, Key Vault, jumpbox, and the
VNet-injected Foundry account/project are all deployed — and the Fabric
workspace, Eventhouse, Eventstream, and Operations Agent are all created and
wired together automatically right after. Confirm telemetry is already
flowing:

```bash
.venv/bin/python src/fleetops/validate/smoke_kql.py
```

### Step 3 — Lock the Fabric workspace to private-only access

```bash
./infra/deploy.ps1 -Wave 2
```

Then, from the jumpbox (see **Where to run these** below):

```bash
python3 src/fleetops/validate/network_check.py
```

Once that confirms DNS resolves privately, run this from your own machine:

```bash
.venv/bin/python src/fleetops/setup/05_network_policy.py --confirm
```

### Step 4 — Deploy the Foundry hosted agent and publish to Teams

```bash
.venv/bin/python src/fleetops/foundry/deploy_hosted_agent.py
./infra/deploy.ps1 -Wave 3
.venv/bin/python src/fleetops/foundry/publish_teams.py
```

If the first command fails with a `ProvisioningError` that doesn't clear on
retry, use the image-based fallback instead — see **How it works** for the
exact sequence.

**Result:** the last command prints a direct Teams link —
`https://teams.microsoft.com/l/app/<teamsAppId>`. That's the end result of
this whole step: open it, and message the agent.

### Step 5 — Start the Operations Agent (manual, one-time, portal-only)

Its item and real monitoring instructions were already created automatically
in Step 2 — this is the only part of the whole deployment with no API, so it
has to be a human clicking two buttons:

1. Open the [Fabric portal](https://app.fabric.microsoft.com), go to your
   workspace (`fleet-ops-copilot` by default), and open the **Fleet
   Operations Monitor** item.
2. Click **Generate Playbook**.
3. Click **Start**.

**Result:** the agent begins evaluating `BusTelemetry` on its own schedule
and will alert on delay incidents, dwell/holding incidents, and vehicles
that stop reporting.

### Step 6 — Validate end to end

From the jumpbox:

```bash
python3 src/fleetops/validate/e2e_flow.py
```

### You're done

- **Operations Agent**: running, watching `BusTelemetry`, alerting on
  incidents.
- **Conversational agent**: live in Microsoft Teams at the link Step 4
  printed. Send it a question about a vehicle or route — it queries the
  Eventhouse directly and answers from live data.

### Where to run these from

Two kinds of machine come up above:

- **local** — your own machine, wherever you ran `azd provision` from.
  Fine for plain Azure/Fabric control-plane calls.
- **jumpbox** — the Foundry account's data-plane API and the workspace's
  *private* endpoint are only reachable from inside the VNet. Copy the repo
  onto the jumpbox once, then run scripts there:
  ```bash
  az container exec --resource-group <rg> --name ci-fleetops-jump \
    --container-name jumpbox --exec-command "python3 /path/to/script.py"
  ```
  (See **How it works** for exactly which steps need which, and why.)

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

---

## How it works

Everything above is what to do. This section is the *why* — platform quirks,
timing-sensitive ordering, and the exact error messages you'll see if a step
is skipped or run out of order. Nothing here is required reading to deploy;
it exists for when something needs debugging.

### Why the Foundry account has to be public while deploying and publishing

**The single most important, least obvious fact in this whole repo.** The
Foundry account's hosted-agent invocation routes (`/endpoint/protocols/openai/`,
and — critically — the `activityProtocol` route Teams itself calls) only get
correctly registered in Azure's internal routing/DNS layer if the account is
publicly reachable (`publicNetworkAccess: Enabled`, `networkAcls.defaultAction:
Allow`) at the time the agent is deployed and published. An account created
and left `Disabled` from the start — the normal, secure-by-default state this
repo's Bicep produces — hits a **permanent, not-transient** `403 Traffic is
not from an approved private endpoint` (from inside the VNet) or `404
Subdomain does not map to a resource` (from outside it) on every invocation
attempt, forever, regardless of how the account, Bot Service, or Teams
publish are configured. This isn't a propagation delay — a real deployment
left in this state for hours never recovers. Confirmed by direct A/B test:
the identical agent, identical Bot Service, identical Teams publish call,
failed permanently when the account was `Disabled` from creation, and worked
immediately when the account was flipped `Enabled` right after creation,
before the agent was ever deployed.

The fix costs nothing extra in the end state — it's purely about
**ordering**, and both scripts that touch this handle it automatically:
`deploy_hosted_agent.py` (Step 4) flips the account public before doing
anything else, and leaves it public (Bot Service and Teams publish both
still need it). `publish_teams.py` (also Step 4) flips it back to private as
its last action, once publishing has actually succeeded. You don't need to
run any `az resource update` yourself — pass `--skip-network-toggle` to
either script only if you're deliberately managing this by hand (e.g.
re-running Step 4 after a failed attempt where the account is already
public).

The account is still VNet-injected the whole time (`networkInjections` with
the `agent` scenario pointing at `snet-agent` isn't affected by this
toggle), so its own connection to the Fabric Eventhouse stays on the private
link throughout — only the account's own inbound reachability changes.
Locking back down takes a few minutes to actually take effect (confirmed
live: a direct public call briefly still succeeded right after the toggle,
then started correctly returning `403 Public access is disabled` a few
minutes later) — that's an ordinary propagation delay, not a sign anything
is wrong. Once locked down, real Teams/Bot Service traffic continues working
via the `enable_m365_public_endpoint` exception set during Teams publish;
only the account's general public reachability closes.

### `deploy_hosted_agent.py`: RBAC grant and the ACR fallback

This step also grants the new agent's identity Fabric workspace/Kusto
access, which needs a delegated human session — the jumpbox's own identity
only has enough Foundry permissions for the deploy call itself. If you
haven't done an interactive `az login` on this particular jumpbox container,
that grant will fail with `403 InsufficientPrivileges` (the deploy still
succeeds); re-run just the grant from your own machine — the failure message
tells you exactly how.

Separately, if this step fails with a `ProvisioningError` that doesn't clear
on retry, that's a platform-side issue with the source-upload build path
specifically — fall back to building and pushing an image instead of
building from source:

1. Deploy `infra/modules/foundry.bicep` with `enableContainerRegistry=true`
   (creates a private ACR).
2. `az acr update --name <acrName> --public-network-enabled true` —
   **temporarily makes the registry publicly reachable.** ACR Tasks' own
   build agent runs on Azure's general-purpose IP range, not yours, so
   there's no narrower allowlist option than this for a registry that's
   otherwise private-endpoint-only. Do this only for the few minutes the
   build takes.
3. `az acr build --registry <acrName> --image fleet-incident-agent:v1 --platform linux/amd64 src/fleetops/foundry/hosted_agent`
4. `az acr update --name <acrName> --public-network-enabled false` —
   **put it back.** Don't skip this.
5. `python deploy_hosted_agent.py --image <acrName>.azurecr.io/fleet-incident-agent:v1`

See `deploy_hosted_agent.py`'s module docstring for the same sequence inline
with the code that consumes it.

### `publish_teams.py`: the deep link, and the delegated-token requirement

The script's own log output prints a direct link once publishing succeeds —
`https://teams.microsoft.com/l/app/<teamsAppId>` — that's a real, working
deep link straight to the agent, not something you need to construct
yourself. `titleId` (also printed) isn't enough on its own to build one; you
need `teamsAppId` from the same publish response, which this script captures
into `state.json['teams_publish']`.

This call needs a genuinely delegated (human) token, not the jumpbox's own
managed identity — confirmed live as a `400 AADSTS500016 (... is not
supported as a resource application to execute the flow)` from the
`microsoft365/publish` endpoint specifically, because it does an
on-behalf-of exchange that a managed-identity token can't participate in.
`DefaultAzureCredential` on the jumpbox silently prefers the managed
identity over any interactive `az login` you've done on that container, so
run this one step with `FLEETOPS_FORCE_CLI_CREDENTIAL=1` to force the CLI
credential instead. From your own machine (not the jumpbox),
`DefaultAzureCredential` already falls back to the CLI credential on its own
— no env var needed there.

### Where scripts run from, in detail

Some of these steps are plain Azure/Fabric **control-plane** REST calls,
signed in as yourself — they don't need to run from inside the VNet, so your
own machine is fine (**local**). The rest either test the workspace's
*private* endpoint from inside the network, or call the Foundry account's
own data-plane API when it's not currently public — and genuinely fail with
`403 Public access is disabled` from anywhere but inside the VNet
(**jumpbox**). Copy the repo onto the jumpbox once (`curl`+`tar`, since
`git clone` doesn't reliably complete over `az container exec`), then run
scripts there the same way shown above.

### Operations Agent: why the definition can be reused indefinitely

Its schema isn't publicly documented in enough detail to author one blind,
so this repo captured a real definition from the portal once — the actual
instructions (three monitoring rules over `BusTelemetry`), committed at
`artifacts/ops-agent/OperationsAgentV1.json`. From that point on, every
deployment — including a full teardown-and-rebuild — can create the item via
the documented `POST .../operationsAgents` API using that same file, with no
portal authoring ever needed again: `06_ops_agent.py --apply` (wired into
the `postprovision` hook) does exactly this.

The one thing that *does* change every time the Eventhouse is recreated is
the `dataSources` block's `workspaceId`/`kqlDatabaseId` — those are
per-deployment IDs, not part of the authored config. `06_ops_agent.py`
re-targets them at whatever Eventhouse exists in the current environment on
every `--apply`/`--update` run, so the committed file never goes stale.

To edit the instructions themselves, edit
`artifacts/ops-agent/OperationsAgentV1.json` directly and push with
`06_ops_agent.py --update <opsAgentId>` (the ID is in `state.json`, or in
the portal URL bar). `--capture <id>` still exists for the rare case of
pulling a portal-made edit back into the repo. **Generate Playbook** and
**Start** remain genuinely portal-only actions with no API equivalent at
all — that's why Step 5 above is a manual click.

### Delegated sign-in, generally

Every setup/validation script must run under a real operator's own
`az login` session, not a service principal — the Operations Agent inherits
its creator's identity, and the workspace network lockdown is sensitive
enough that it should always have a human's fingerprints on it.
